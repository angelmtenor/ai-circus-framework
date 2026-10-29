"""Tests for the policy decision points (noop / static / OPA / HTTP) and the decision contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from llm_gateway.policy import (
    ALLOW,
    HttpDecider,
    NoopDecider,
    OpaDecider,
    PolicyError,
    PolicyInput,
    StaticDecider,
    build_decider,
    parse_decision,
)

SCHEMAS = Path(__file__).resolve().parents[3] / "docs" / "governance" / "schemas"
INPUT = PolicyInput(tenant_id="org-1", ai_system_id="churn", model="gpt-4o-mini", call_type="completion")


def _decide(decider: Any, policy_input: PolicyInput = INPUT) -> Any:
    return asyncio.run(decider.decide(policy_input))


def test_noop_allows_everything() -> None:
    assert _decide(NoopDecider()) is ALLOW


def test_parse_decision_accepts_a_full_decision_and_applies_defaults() -> None:
    minimal = parse_decision({"decision": "allow", "reason_code": "ok"})
    assert minimal.obligations.status == "active" and minimal.obligations.allowed_models is None

    full = parse_decision({
        "decision": "degrade",
        "reason_code": "cost_control",
        "policy_version": "v7",
        "obligations": {
            "allowed_models": ["a", "b"],
            "max_tokens": 200,
            "rpm": 5,
            "monthly_budget": 12,
            "status": "active",
        },
    })
    assert full.obligations.allowed_models == ("a", "b") and full.obligations.monthly_budget == pytest.approx(12.0)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {"decision": "maybe", "reason_code": "x"},
        {"decision": "allow"},
        {"decision": "allow", "reason_code": "Has Spaces"},
        {"decision": "allow", "reason_code": "ok", "extra": 1},
        {"decision": "allow", "reason_code": "ok", "obligations": {"max_tokens": 0}},
        {"decision": "allow", "reason_code": "ok", "obligations": {"max_tokens": True}},
        {"decision": "allow", "reason_code": "ok", "obligations": {"status": "paused"}},
        {"decision": "allow", "reason_code": "ok", "obligations": {"monthly_budget": -1}},
        {"decision": "allow", "reason_code": "ok", "obligations": {"allowed_models": "gpt"}},
        {"decision": "allow", "reason_code": "ok", "obligations": {"roles": ["admin"]}},
    ],
)
def test_parse_decision_rejects_anything_off_contract(bad: object) -> None:
    with pytest.raises(PolicyError):
        parse_decision(bad)


def test_decisions_match_the_published_json_schemas() -> None:
    """The gateway re-implements the contract (it cannot import ai_circus_shared); the published
    schemas are the source of truth, so what it emits and accepts must validate against them.
    """
    decision_schema = json.loads((SCHEMAS / "policy-decision.json").read_text())
    input_schema = json.loads((SCHEMAS / "policy-input.json").read_text())
    Draft202012Validator(input_schema).validate(INPUT.to_json())
    for raw in (
        {"decision": "allow", "reason_code": "default_allow"},
        {
            "decision": "degrade",
            "reason_code": "cost_control",
            "policy_version": "v1",
            "obligations": {
                "allowed_models": ["a"],
                "max_tokens": 10,
                "rpm": 2,
                "monthly_budget": 1.5,
                "status": "suspended",
            },
        },
    ):
        Draft202012Validator(decision_schema).validate(raw)
        Draft202012Validator(decision_schema).validate(parse_decision(raw).to_json())
    assert set(input_schema["properties"]) == set(INPUT.to_json())


def _rules(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(text)
    return path


RULES = """
version: "2026-10-29"
rules:
  - tenant: org-1
    ai_system: churn
    decision: degrade
    reason_code: cost_control
    obligations: {allowed_models: [cheap-model], max_tokens: 100}
  - tenant: "*"
    ai_system: titanic
    obligations: {status: suspended}
  - tenant: org-2
    decision: deny
    reason_code: contract_ended
"""


def test_static_first_matching_rule_wins_and_carries_the_version(tmp_path: Path) -> None:
    decider = StaticDecider(_rules(tmp_path, RULES))

    degraded = _decide(decider)
    assert (degraded.decision, degraded.reason_code, degraded.policy_version) == (
        "degrade",
        "cost_control",
        "2026-10-29",
    )
    assert degraded.obligations.allowed_models == ("cheap-model",)
    suspended = _decide(decider, PolicyInput("anyone", "titanic", "m", "completion"))
    assert suspended.obligations.status == "suspended"
    assert _decide(decider, PolicyInput("org-2", "whatever", "m", "completion")).decision == "deny"
    fallthrough = _decide(decider, PolicyInput("org-9", "churn", "m", "completion"))
    assert (fallthrough.decision, fallthrough.reason_code) == ("allow", "no_matching_rule")


def test_static_reloads_when_the_file_changes(tmp_path: Path) -> None:
    path = _rules(tmp_path, RULES)
    decider = StaticDecider(path)
    assert _decide(decider).decision == "degrade"

    path.write_text("rules:\n  - {tenant: org-1, ai_system: churn, decision: deny, reason_code: stop}\n")
    import os

    os.utime(path, (path.stat().st_mtime + 5, path.stat().st_mtime + 5))
    assert _decide(decider).reason_code == "stop"


def test_static_a_broken_or_missing_file_is_a_policy_error_not_an_allow(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        _decide(StaticDecider(tmp_path / "missing.yaml"))
    with pytest.raises(PolicyError):
        _decide(StaticDecider(_rules(tmp_path, "rules: [1]")))
    with pytest.raises(PolicyError):
        _decide(StaticDecider(_rules(tmp_path, "rules:\n  - {decision: sometimes}\n")))


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_http_decider_posts_the_input_and_validates_the_answer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"decision": "deny", "reason_code": "blocked"})

    decider = HttpDecider("http://pdp/decide", token="t0ken", client=_client(handler))  # ruff: ignore[hardcoded-password-func-arg]
    decision = _decide(decider)

    assert decision.decision == "deny"
    assert json.loads(seen[0].content) == INPUT.to_json()
    assert seen[0].headers["authorization"] == "Bearer t0ken"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"decision": "bogus", "reason_code": "x"}),
    ],
)
def test_http_decider_failures_are_policy_errors(response: httpx.Response) -> None:
    with pytest.raises(PolicyError):
        _decide(HttpDecider("http://pdp", client=_client(lambda request: response)))


def test_http_decider_transport_failure_is_a_policy_error() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(PolicyError):
        _decide(HttpDecider("http://pdp", client=_client(down)))


def test_opa_wraps_input_and_unwraps_result_at_the_package_path() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"result": {"decision": "allow", "reason_code": "opa_ok", "policy_version": "r1"}}
        )

    decision = _decide(OpaDecider("http://opa:8181/", package="/aicircus/governance/", client=_client(handler)))

    assert decision.reason_code == "opa_ok" and decision.policy_version == "r1"
    assert str(seen[0].url) == "http://opa:8181/v1/data/aicircus/governance/decision"
    assert json.loads(seen[0].content) == {"input": INPUT.to_json()}


def test_opa_undefined_result_is_an_error_so_a_policy_gap_hits_the_fail_mode() -> None:
    with pytest.raises(PolicyError, match="undefined"):
        _decide(OpaDecider("http://opa", client=_client(lambda request: httpx.Response(200, json={}))))


def test_build_decider_selects_by_name_and_refuses_incomplete_config() -> None:
    class Cfg:
        POLICY_DECIDER = None
        POLICY_STATIC_PATH = None
        POLICY_OPA_URL = None
        POLICY_OPA_PACKAGE = None
        POLICY_HTTP_URL = None
        POLICY_HTTP_TOKEN = None
        POLICY_TIMEOUT_MS = None

    cfg = Cfg()
    assert isinstance(build_decider(cfg), NoopDecider)
    for kind, field, cls in (
        ("static", "POLICY_STATIC_PATH", StaticDecider),
        ("opa", "POLICY_OPA_URL", OpaDecider),
        ("http", "POLICY_HTTP_URL", HttpDecider),
    ):
        cfg.POLICY_DECIDER = kind
        with pytest.raises(ValueError, match=field):
            build_decider(cfg)
        setattr(cfg, field, "/x.yaml" if kind == "static" else "http://x")
        assert isinstance(build_decider(cfg), cls)
    cfg.POLICY_DECIDER = "magic"
    with pytest.raises(ValueError, match="Unknown POLICY_DECIDER"):
        build_decider(cfg)
