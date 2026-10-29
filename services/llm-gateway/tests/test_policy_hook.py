"""Tests for policy enforcement at the gateway (policy_hook.py): allow / deny / degrade, the Valkey
decision cache and invalidation, every fail mode, rpm / budget obligations and the latency budget.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator

from llm_gateway import policy_hook
from llm_gateway.governance_events import OUTBOX_STREAM, GatewayEvents
from llm_gateway.policy import NoopDecider, PolicyDecision, PolicyError, PolicyInput, parse_decision
from llm_gateway.policy_hook import PolicyEnforcer, decision_cache_key

SCHEMAS = Path(__file__).resolve().parents[3] / "docs" / "governance" / "schemas"


@dataclass
class ScriptedDecider:
    """A decider returning a canned decision (or raising), counting how often it is asked."""

    answer: PolicyDecision | Exception
    calls: list[PolicyInput] = field(default_factory=list)
    name: str = "scripted"

    async def decide(self, policy_input: PolicyInput) -> PolicyDecision:
        """Return or raise the scripted answer."""
        self.calls.append(policy_input)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _decision(decision: str = "allow", reason: str = "ok", **obligations: Any) -> PolicyDecision:
    return parse_decision({
        "decision": decision,
        "reason_code": reason,
        "policy_version": "v1",
        "obligations": obligations,
    })


def _data(model: str = "gpt-4o-mini", org: str | None = "org-1", slug: str = "churn", **extra: Any) -> dict:
    data: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "secret prompt"}],
        "metadata": {"trace_metadata": {"scenario_slug": slug}},
        **extra,
    }
    if org is not None:
        data["user"] = org
    return data


@dataclass
class Rig:
    """An enforcer wired to fakes, plus the handles tests inspect."""

    enforcer: PolicyEnforcer
    decider: ScriptedDecider
    client: Any
    outbox: Any

    def events(self) -> list[dict[str, Any]]:
        """The governance events the enforcer raised, oldest first."""
        return [json.loads(fields[b"event"]) for _id, fields in self.outbox.xrange(OUTBOX_STREAM)]

    async def call(self, data: dict, call_type: str = "completion") -> dict | None:
        """Run the pre-call hook."""
        return await self.enforcer.async_pre_call_hook(None, None, data, call_type)  # type: ignore[arg-type]


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def _rig(answer: PolicyDecision | Exception, *, fail_mode: str = "open", ttl: int = 30, events: bool = True) -> Rig:
    decider = ScriptedDecider(answer)
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    outbox = fakeredis.FakeRedis()
    enforcer = PolicyEnforcer(
        decider=decider,
        client=client,
        events=GatewayEvents(enabled=events, sink="http", client=outbox),
        fail_mode=fail_mode,
        ttl_seconds=ttl,
    )
    return Rig(enforcer, decider, client, outbox)


def test_noop_is_a_true_no_op_and_touches_nothing() -> None:
    async def scenario() -> None:
        enforcer = PolicyEnforcer(decider=NoopDecider(), events=GatewayEvents())
        data = _data()
        before = json.dumps(data, sort_keys=True)
        assert await enforcer.async_pre_call_hook(None, None, data, "completion") is None  # type: ignore[arg-type]
        assert json.dumps(data, sort_keys=True) == before
        assert enforcer._client is None, "noop must not even open a Valkey connection"
        await enforcer.async_log_success_event({"user": "org-1", "response_cost": 1.0}, None, None, None)

    _run(scenario)


def test_allow_passes_the_request_through_unchanged() -> None:
    async def scenario() -> None:
        rig = _rig(_decision())
        data = _data()
        before = json.dumps(data, sort_keys=True)
        assert await rig.call(data) is None
        assert json.dumps(data, sort_keys=True) == before
        assert rig.events() == [], "allowing is the normal case and is not an event"

    _run(scenario)


def test_deny_is_a_403_with_the_reason_code_and_a_content_free_event() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("deny", "contract_ended"))
        with pytest.raises(HTTPException) as refused:
            await rig.call(_data())
        assert refused.value.status_code == 403
        assert "contract_ended" in refused.value.detail

        (event,) = rig.events()
        assert event["type"] == "dev.aicircus.governance.policy.decision.v1"
        assert event["data"] | {} == {
            "tenant_id": "org-1",
            "ai_system_id": "churn",
            "model": "gpt-4o-mini",
            "call_type": "completion",
            "decision": "deny",
            "reason_code": "contract_ended",
            "policy_version": "v1",
        }
        assert "secret prompt" not in json.dumps(event)
        schema = json.loads((SCHEMAS / "event-policy-decision.v1.json").read_text())
        Draft202012Validator(schema).validate(event["data"])
        Draft202012Validator(json.loads((SCHEMAS / "cloudevent-envelope.json").read_text())).validate(event)

    _run(scenario)


def test_a_suspended_ai_system_is_refused_whatever_the_decision_says() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok", status="suspended"))
        with pytest.raises(HTTPException) as refused:
            await rig.call(_data())
        assert refused.value.status_code == 403 and "ai_system_suspended" in refused.value.detail
        assert rig.events()[0]["data"]["reason_code"] == "ai_system_suspended"

    _run(scenario)


def test_degrade_swaps_the_model_caps_tokens_and_says_so() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("degrade", "cost_control", allowed_models=["cheap", "other"], max_tokens=100))
        data = _data(model="gpt-5.6-sol", max_tokens=4000)
        result = await rig.call(data)
        assert result is data
        assert data["model"] == "cheap" and data["max_tokens"] == 100
        (event,) = rig.events()
        assert event["data"]["decision"] == "degrade"
        assert event["data"]["served_model"] == "cheap" and event["data"]["max_tokens"] == 100

        # No caller-supplied cap: the policy cap becomes the limit; a lower caller cap is kept.
        absent = _data(model="cheap")
        await rig.call(absent)
        assert absent["max_tokens"] == 100
        lower = _data(model="cheap", max_tokens=50)
        await rig.call(lower)
        assert lower["max_tokens"] == 50

    _run(scenario)


def test_token_cap_is_never_applied_to_an_embedding_call() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok", max_tokens=10))
        data = _data(model="local-embed")
        await rig.call(data, call_type="embedding")
        assert "max_tokens" not in data

    _run(scenario)


def test_allow_with_an_unlisted_model_is_denied_but_listed_models_pass() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok", allowed_models=["gpt-4o-mini"]))
        assert await rig.call(_data(model="gpt-4o-mini")) is None
        with pytest.raises(HTTPException) as refused:
            await rig.call(_data(model="gpt-5.6-sol"))
        assert refused.value.status_code == 403 and "model_not_allowed" in refused.value.detail

    _run(scenario)


def test_cache_miss_asks_the_decider_then_hits_serve_from_valkey_with_a_ttl() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok"), ttl=30)
        await rig.call(_data())
        await rig.call(_data())
        await rig.call(_data())
        assert len(rig.decider.calls) == 1, "second and third calls are cache hits"

        key = decision_cache_key("org-1", "churn")
        assert key == "tenant-org-1:policy:churn", "same tenant-prefixed key format as budget_hook / TenantCache"
        assert 0 < await rig.client.ttl(key) <= 30
        assert json.loads(await rig.client.hget(key, "gpt-4o-mini"))["decision"] == "allow"

        await rig.call(_data(model="another-model"))
        assert len(rig.decider.calls) == 2, "decisions are cached per model"

    _run(scenario)


def test_decisions_are_scoped_per_tenant_and_ai_system() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok"))
        await rig.call(_data(org="org-1", slug="churn"))
        await rig.call(_data(org="org-2", slug="churn"))
        await rig.call(_data(org="org-1", slug="titanic"))
        assert len(rig.decider.calls) == 3

    _run(scenario)


def test_invalidation_by_deleting_the_hash_forces_a_fresh_decision() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok"))
        await rig.call(_data())
        await rig.client.delete(decision_cache_key("org-1", "churn"))  # what the invalidate webhook does
        await rig.call(_data())
        assert len(rig.decider.calls) == 2

    _run(scenario)


def test_a_pushed_decision_overwrites_the_cache_for_every_model() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok"))
        await rig.call(_data())
        key = decision_cache_key("org-1", "churn")
        pushed = _decision("deny", "suspended_by_governance", status="suspended")
        await rig.client.delete(key)
        await rig.client.hset(key, "*", json.dumps(pushed.to_json()))  # what a pushed overwrite does
        with pytest.raises(HTTPException) as refused:
            await rig.call(_data())
        assert refused.value.status_code == 403
        assert len(rig.decider.calls) == 1, "the pushed decision answered; the decider was not asked again"

    _run(scenario)


def test_a_corrupt_cache_entry_is_ignored_not_trusted() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok"))
        await rig.client.hset(decision_cache_key("org-1", "churn"), "gpt-4o-mini", '{"decision": "bogus"}')
        assert await rig.call(_data()) is None
        assert len(rig.decider.calls) == 1

    _run(scenario)


def test_fail_open_lets_the_call_through_logs_and_raises_an_event() -> None:
    async def scenario() -> None:
        rig = _rig(PolicyError("pdp down"), fail_mode="open")
        assert await rig.call(_data()) is None
        (event,) = rig.events()
        assert event["data"]["fail_open"] is True and event["data"]["decision"] == "allow"
        assert event["data"]["reason_code"] == "policy_unavailable"

    _run(scenario)


def test_fail_closed_refuses_with_503() -> None:
    async def scenario() -> None:
        rig = _rig(PolicyError("pdp down"), fail_mode="closed")
        with pytest.raises(HTTPException) as refused:
            await rig.call(_data())
        assert refused.value.status_code == 503 and "policy_unavailable" in refused.value.detail
        assert rig.events()[0]["data"].get("fail_open") is None

    _run(scenario)


@pytest.mark.parametrize(
    "risk,refused", [("high", True), ("prohibited", True), ("limited", False), ("minimal", False), (None, False)]
)
def test_by_risk_is_closed_for_high_risk_systems_and_open_otherwise(risk: str | None, refused: bool) -> None:
    async def scenario() -> None:
        rig = _rig(PolicyError("pdp down"), fail_mode="by_risk")
        if risk is not None:
            await rig.client.set("governance:ai-system:churn", json.dumps({"risk_category": risk}))
        if refused:
            with pytest.raises(HTTPException) as err:
                await rig.call(_data())
            assert err.value.status_code == 503
        else:
            assert await rig.call(_data()) is None

    _run(scenario)


def test_an_unexpected_decider_exception_is_treated_as_unavailable() -> None:
    async def scenario() -> None:
        rig = _rig(RuntimeError("boom"), fail_mode="closed")
        with pytest.raises(HTTPException) as refused:
            await rig.call(_data())
        assert refused.value.status_code == 503

    _run(scenario)


def test_a_failed_call_is_not_cached() -> None:
    async def scenario() -> None:
        rig = _rig(PolicyError("pdp down"))
        await rig.call(_data())
        await rig.call(_data())
        assert len(rig.decider.calls) == 2

    _run(scenario)


def test_calls_without_a_tenant_or_ai_system_are_not_governed() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("deny", "never"))
        assert await rig.call(_data(org=None)) is None
        no_system = {"model": "m", "user": "org-1", "metadata": {}}
        assert await rig.call(no_system) is None
        assert rig.decider.calls == []

    _run(scenario)


@pytest.mark.parametrize("org,slug", [("org 1", "churn"), ("org-1", "Churn!"), ("org:1", "churn"), ("o" * 65, "churn")])
def test_unsafe_identifiers_are_skipped_never_used_in_keys(org: str, slug: str) -> None:
    async def scenario() -> None:
        rig = _rig(_decision("deny", "never"))
        assert await rig.call(_data(org=org, slug=slug)) is None
        assert rig.decider.calls == []
        assert await rig.client.keys("*") == []

    _run(scenario)


def test_explicit_ai_system_id_in_metadata_beats_the_scenario_slug() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok"))
        data = _data(slug="churn")
        data["metadata"]["aicf_ai_system_id"] = "churn-v2"
        await rig.call(data)
        assert rig.decider.calls[0].ai_system_id == "churn-v2"

    _run(scenario)


def test_rpm_obligation_refuses_the_call_over_the_limit() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok", rpm=2))
        await rig.call(_data())
        await rig.call(_data())
        with pytest.raises(HTTPException) as refused:
            await rig.call(_data())
        assert refused.value.status_code == 429 and "rate_limited" in refused.value.detail

    _run(scenario)


def test_ai_system_budget_obligation_blocks_once_spend_reaches_it_and_spend_is_tracked() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok", monthly_budget=1.0))
        assert await rig.call(_data()) is None

        success = {"user": "org-1", "response_cost": 0.6, "litellm_params": _data()}
        await rig.enforcer.async_log_success_event(success, None, None, None)
        assert await rig.call(_data()) is None, "under budget"
        await rig.enforcer.async_log_success_event(success, None, None, None)

        with pytest.raises(HTTPException) as refused:
            await rig.call(_data())
        assert refused.value.status_code == 429 and "ai_system_budget_exhausted" in refused.value.detail
        types = [e["type"].split(".governance.")[1] for e in rig.events()]
        assert "budget.exhausted.v1" in types

        other = _data(org="org-2")
        assert await rig.call(other) is None, "another tenant's spend is separate"

    _run(scenario)


def test_repeated_denials_raise_one_event_per_minute_not_one_per_request() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("deny", "contract_ended"))
        for _ in range(5):
            with pytest.raises(HTTPException):
                await rig.call(_data())
        assert len(rig.events()) == 1

    _run(scenario)


def test_events_off_means_no_events_but_enforcement_is_unchanged() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("deny", "x"), events=False)
        with pytest.raises(HTTPException):
            await rig.call(_data())
        assert rig.events() == []

    _run(scenario)


def test_a_valkey_outage_on_the_cache_falls_back_to_the_decision_point() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("deny", "contract_ended"))

        async def broken(*args: Any, **kwargs: Any) -> Any:  # ruff: ignore[unused-async]
            raise policy_hook.aioredis.RedisError("down")

        rig.enforcer._client.hmget = broken  # type: ignore[method-assign]
        rig.enforcer._client.pipeline = lambda: (_ for _ in ()).throw(policy_hook.aioredis.RedisError("down"))  # type: ignore[method-assign]
        with pytest.raises(HTTPException):
            await rig.call(_data())

    _run(scenario)


def test_unknown_fail_mode_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="POLICY_FAIL_MODE"):
        PolicyEnforcer(
            decider=ScriptedDecider(_decision()), client=fakeredis.aioredis.FakeRedis(), fail_mode="sometimes"
        )


def test_cache_hit_overhead_is_far_below_5ms_p95() -> None:
    async def scenario() -> None:
        rig = _rig(_decision("allow", "ok"))
        await rig.call(_data())  # warm: this one asks the decider and fills the cache
        timings = []
        for _ in range(400):
            started = time.perf_counter()
            await rig.call(_data())
            timings.append((time.perf_counter() - started) * 1000)
        p95 = statistics.quantiles(timings, n=20)[18]
        assert p95 < 5.0, f"p95 {p95:.2f} ms on a cache hit"
        assert len(rig.decider.calls) == 1

    _run(scenario)
