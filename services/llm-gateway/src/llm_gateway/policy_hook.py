"""
- Title:    Policy decision enforcement at the AI gateway
- Author:   Angel Martinez-Tenor

A LiteLLM `CustomLogger` (the same stock extension point `budget_hook.py` uses, registered next to
it in `litellm_settings.callbacks`) that asks a pluggable policy decision point — see policy.py —
whether a tenant's AI system may make this call, and enforces the answer *before* the call is sent:

- `deny` (or an obligation of `status: suspended`) → 403 with the `reason_code`;
- `degrade` → serve the first `allowed_models` entry instead of an unlisted model, and cap tokens;
- `allow` → pass through, still honouring `allowed_models`, `max_tokens`, `rpm`, `monthly_budget`.

Off by default and cheap when off: `POLICY_DECIDER` unset or `noop` returns before any work, so the
gateway behaves exactly as it did. It composes with `budget_hook` (tenant-wide spend caps) rather
than replacing it; the policy runs first so a denied call is never billed.

Calls with no tenant (`user`) or no AI system are not governed: the hook needs both to key a
decision. The AI system id comes from the request `metadata` the calling services already send
(`aicf_ai_system_id`, else the scenario slug in `trace_metadata`).

Decisions are cached in Valkey as a hash at `tenant-{org}:policy:{ai_system}` (field = model, `*` =
any model) with a TTL, so a cache hit is one round trip. `POST /governance/policy/invalidate` on
data-platform-manager deletes the hash — or overwrites the `*` field with a pushed decision — so a
governance tool never has to be polled. This module is self-contained: it cannot import
`ai_circus_shared` (see budget_hook.py for why).

**Fail modes** (`POLICY_FAIL_MODE`) apply when the decision point is unreachable or answers
nonsense: `open` lets the call through, `closed` refuses it (503), `by_risk` refuses it only for
AI systems whose declared EU AI Act risk category is `high`/`prohibited` (read from the catalogue
platform-registry publishes at `governance:ai-system:{id}`). Every fail-open is logged at WARNING
and raised as a `policy.decision` event with `fail_open: true`.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth
from litellm.types.utils import CallTypesLiteral

from llm_gateway import get_env_config, get_logger
from llm_gateway.governance_events import GatewayEvents
from llm_gateway.policy import (
    NoopDecider,
    PolicyDecider,
    PolicyDecision,
    PolicyError,
    PolicyInput,
    build_decider,
    parse_decision,
)

logger = get_logger(__name__)

_ORG_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_AI_SYSTEM_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_DEFAULT_TTL_SECONDS = 30
_COUNTER_TTL_SECONDS = 60 * 60 * 24 * 60
_EVENT_THROTTLE_SECONDS = 60.0
_HIGH_RISK = frozenset({"high", "prohibited"})
CATALOG_KEY_PREFIX = "governance:ai-system"


def _tenant_key(org_id: str, key: str) -> str:
    """Mirrors ai_circus_shared.cache.TenantCache._key exactly (see budget_hook.py)."""
    return f"tenant-{org_id}:{key}"


def decision_cache_key(org_id: str, ai_system_id: str) -> str:
    """The Valkey hash holding the cached decisions of one tenant's AI system."""
    return _tenant_key(org_id, f"policy:{ai_system_id}")


def _month() -> str:
    return f"{datetime.now(UTC):%Y-%m}"


def _ai_system_of(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    explicit = metadata.get("aicf_ai_system_id")
    if isinstance(explicit, str):
        return explicit
    trace = metadata.get("trace_metadata")
    slug = trace.get("scenario_slug") if isinstance(trace, dict) else None
    return slug if isinstance(slug, str) else None


def _is_completion(call_type: str) -> bool:
    return "completion" in call_type or "response" in call_type


class PolicyEnforcer(CustomLogger):
    """See module docstring. `litellm_config.yaml` references the module-level `instance` below."""

    def __init__(
        self,
        *,
        decider: PolicyDecider | None = None,
        client: aioredis.Redis | None = None,
        events: GatewayEvents | None = None,
        fail_mode: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Build the enforcer; every argument defaults from the gateway config (tests inject fakes)."""
        super().__init__()
        config = get_env_config()
        self._decider: PolicyDecider = decider if decider is not None else build_decider(config)
        self._noop = isinstance(self._decider, NoopDecider)
        mode = (fail_mode or getattr(config, "POLICY_FAIL_MODE", None) or "open").strip().lower()
        if mode not in {"open", "closed", "by_risk"}:
            raise ValueError(f"Unknown POLICY_FAIL_MODE={mode!r}; use open, closed or by_risk")
        self._fail_mode = mode
        self._ttl = ttl_seconds or int(getattr(config, "POLICY_CACHE_TTL_SECONDS", None) or _DEFAULT_TTL_SECONDS)
        self._timeout = int(getattr(config, "POLICY_TIMEOUT_MS", None) or 500) / 1000
        self._client = client
        if self._client is None and not self._noop:
            self._client = aioredis.Redis.from_url(config.CACHE_URL, decode_responses=True)
        self._events = events if events is not None else self._build_events(config)
        self._recent_events: dict[tuple[str, ...], float] = {}
        if not self._noop:
            logger.info(
                "Policy enforcement on: decider={} fail_mode={} cache_ttl={}s", self._decider.name, mode, self._ttl
            )

    @staticmethod
    def _build_events(config: Any) -> GatewayEvents:
        import redis  # sync client: events are rare and fire-and-forget

        return GatewayEvents.from_config(config, client_factory=lambda: redis.Redis.from_url(config.CACHE_URL))

    # ── pre-call ────────────────────────────────────────────────────────────────────────────

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: Any,
        data: dict,
        call_type: CallTypesLiteral,
    ) -> dict | None:
        """Decide, then enforce. Returns the (possibly degraded) request, or None when unchanged."""
        if self._noop:
            return None
        policy_input = self._input(data, str(call_type))
        if policy_input is None:
            return None
        try:
            decision = await self._decision_for(policy_input)
        except PolicyError as exc:
            await self._decision_point_unavailable(policy_input, exc)
            return None
        return await self._enforce(policy_input, decision, data)

    def _input(self, data: dict, call_type: str) -> PolicyInput | None:
        org_id = data.get("user")
        metadata = data.get("metadata") or data.get("litellm_metadata")
        ai_system_id = _ai_system_of(metadata)
        model = data.get("model")
        if not (isinstance(org_id, str) and isinstance(ai_system_id, str) and isinstance(model, str)):
            return None
        if not (_ORG_ID.match(org_id) and _AI_SYSTEM_ID.match(ai_system_id) and 0 < len(model) <= 128):
            logger.warning("Policy skipped: unusable tenant/AI-system/model identifiers on a call")
            return None
        return PolicyInput(tenant_id=org_id, ai_system_id=ai_system_id, model=model, call_type=call_type[:64])

    async def _decision_for(self, policy_input: PolicyInput) -> PolicyDecision:
        key = decision_cache_key(policy_input.tenant_id, policy_input.ai_system_id)
        cached = await self._cache_get(key, policy_input.model)
        if cached is not None:
            return cached
        try:
            decision = await asyncio.wait_for(self._decider.decide(policy_input), timeout=self._timeout + 0.25)
        except PolicyError:
            raise
        except Exception as exc:  # timeout, transport, anything a custom decider throws
            raise PolicyError(f"decision point failed: {type(exc).__name__}") from exc
        await self._cache_put(key, policy_input.model, decision)
        return decision

    async def _cache_get(self, key: str, model: str) -> PolicyDecision | None:
        assert self._client is not None
        try:
            for raw in await self._client.hmget(key, [model, "*"]):
                if raw:
                    return parse_decision(json.loads(raw))
        except (aioredis.RedisError, ValueError, PolicyError):
            logger.warning("Policy cache read failed — asking the decision point")
        return None

    async def _cache_put(self, key: str, model: str, decision: PolicyDecision) -> None:
        assert self._client is not None
        try:
            pipe = self._client.pipeline()
            pipe.hset(key, model, json.dumps(decision.to_json(), separators=(",", ":")))
            pipe.expire(key, self._ttl, nx=True)
            await pipe.execute()
        except aioredis.RedisError:
            logger.warning("Policy cache write failed — decision not cached")

    # ── enforcement ─────────────────────────────────────────────────────────────────────────

    async def _enforce(self, policy_input: PolicyInput, decision: PolicyDecision, data: dict) -> dict | None:
        obligations = decision.obligations
        if obligations.status == "suspended":
            reason = decision.reason_code if decision.decision == "deny" else "ai_system_suspended"
            self._refuse(policy_input, decision, reason, 403)
        if decision.decision == "deny":
            self._refuse(policy_input, decision, decision.reason_code, 403)

        changed = False
        served_model: str | None = None
        if obligations.allowed_models and policy_input.model not in obligations.allowed_models:
            if decision.decision != "degrade":
                self._refuse(policy_input, decision, "model_not_allowed", 403)
            served_model = obligations.allowed_models[0]
            data["model"] = served_model
            changed = True
        cap_applied: int | None = None
        if obligations.max_tokens and _is_completion(policy_input.call_type):
            for field in ("max_tokens", "max_completion_tokens"):
                current = data.get(field)
                if isinstance(current, int) and not isinstance(current, bool):
                    data[field] = min(current, obligations.max_tokens)
            if not isinstance(data.get("max_tokens"), int) and not isinstance(data.get("max_completion_tokens"), int):
                data["max_tokens"] = obligations.max_tokens
            cap_applied = obligations.max_tokens
            changed = True

        await self._enforce_counters(policy_input, decision)

        if decision.decision == "degrade":
            self._emit(
                policy_input,
                decision,
                "degrade",
                decision.reason_code,
                served_model=served_model,
                max_tokens=cap_applied,
            )
        return data if changed else None

    async def _enforce_counters(self, policy_input: PolicyInput, decision: PolicyDecision) -> None:
        obligations = decision.obligations
        assert self._client is not None
        org, ai = policy_input.tenant_id, policy_input.ai_system_id
        try:
            if obligations.rpm:
                window = _tenant_key(org, f"policy:rpm:{ai}:{int(time.time() // 60)}")
                count = await self._client.incr(window)
                if count == 1:
                    await self._client.expire(window, 120)
                if count > obligations.rpm:
                    self._refuse(policy_input, decision, "rate_limited", 429)
            if obligations.monthly_budget is not None:
                spend = float(await self._client.get(_tenant_key(org, f"policy:spend:{ai}:{_month()}")) or 0.0)
                if spend >= obligations.monthly_budget:
                    self._events.emit(
                        "budget.exhausted",
                        {
                            "org_id": org,
                            "period": _month(),
                            "spent_usd": round(spend, 6),
                            "cap_usd": obligations.monthly_budget,
                            "scope": "ai_system",
                            "ai_system_id": ai,
                        },
                        tenant_id=org,
                        ai_system_id=ai,
                    )
                    self._refuse(policy_input, decision, "ai_system_budget_exhausted", 429)
        except aioredis.RedisError:
            logger.warning("Policy counters unavailable (Valkey) — rpm/budget obligations skipped for this call")

    def _refuse(self, policy_input: PolicyInput, decision: PolicyDecision, reason: str, status: int) -> None:
        self._emit(policy_input, decision, "deny", reason)
        raise HTTPException(
            status_code=status,
            detail=f"Policy refused this call (reason_code={reason}, ai_system={policy_input.ai_system_id}).",
        )

    async def _decision_point_unavailable(self, policy_input: PolicyInput, exc: PolicyError) -> None:
        if self._fail_mode == "closed":
            closed = True
        elif self._fail_mode == "by_risk":
            closed = await self._risk(policy_input.ai_system_id) in _HIGH_RISK
        else:
            closed = False
        if closed:
            logger.error(
                "Policy decision point unavailable ({}) — refusing the call (fail mode {})", exc, self._fail_mode
            )
            self._emit(policy_input, None, "deny", "policy_unavailable")
            raise HTTPException(
                status_code=503, detail="Policy decision point unavailable (reason_code=policy_unavailable)."
            )
        logger.warning(
            "Policy decision point unavailable ({}) — FAIL-OPEN for tenant={} ai_system={} (fail mode {})",
            exc,
            policy_input.tenant_id,
            policy_input.ai_system_id,
            self._fail_mode,
        )
        self._emit(policy_input, None, "allow", "policy_unavailable", fail_open=True)

    async def _risk(self, ai_system_id: str) -> str:
        assert self._client is not None
        try:
            raw = await self._client.get(f"{CATALOG_KEY_PREFIX}:{ai_system_id}")
            return str(json.loads(raw).get("risk_category", "unassessed")) if raw else "unassessed"
        except (aioredis.RedisError, ValueError, AttributeError):
            return "unassessed"

    def _emit(
        self,
        policy_input: PolicyInput,
        decision: PolicyDecision | None,
        outcome: str,
        reason: str,
        *,
        fail_open: bool = False,
        served_model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Raise a `policy.decision` event, at most once a minute per tenant/system/outcome/reason
        so a suspended system under load is one signal, not one per request.
        """
        if not self._events.enabled:
            return
        throttle_key = (policy_input.tenant_id, policy_input.ai_system_id, outcome, reason, str(fail_open))
        now = time.monotonic()
        if now - self._recent_events.get(throttle_key, -_EVENT_THROTTLE_SECONDS) < _EVENT_THROTTLE_SECONDS:
            return
        if len(self._recent_events) > 1000:
            self._recent_events.clear()
        self._recent_events[throttle_key] = now
        self._events.emit(
            "policy.decision",
            {
                "tenant_id": policy_input.tenant_id,
                "ai_system_id": policy_input.ai_system_id,
                "model": policy_input.model,
                "call_type": policy_input.call_type,
                "decision": outcome,
                "reason_code": reason,
                "policy_version": decision.policy_version if decision is not None else None,
                "fail_open": fail_open or None,
                "served_model": served_model,
                "max_tokens": max_tokens,
            },
            tenant_id=policy_input.tenant_id,
            ai_system_id=policy_input.ai_system_id,
        )

    # ── spend tracking for monthly_budget obligations ───────────────────────────────────────

    async def async_log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        """Add this call's cost to the AI system's running monthly spend (for `monthly_budget`)."""
        if self._noop or self._client is None:
            return
        org_id = kwargs.get("user")
        cost = kwargs.get("response_cost")
        ai_system_id = _ai_system_of((kwargs.get("litellm_params") or {}).get("metadata"))
        if not (isinstance(org_id, str) and isinstance(ai_system_id, str) and cost):
            return
        if not (_ORG_ID.match(org_id) and _AI_SYSTEM_ID.match(ai_system_id)):
            return
        key = _tenant_key(org_id, f"policy:spend:{ai_system_id}:{_month()}")
        try:
            pipe = self._client.pipeline()
            pipe.incrbyfloat(key, float(cost))
            pipe.expire(key, _COUNTER_TTL_SECONDS, nx=True)
            await pipe.execute()
        except aioredis.RedisError:
            logger.warning("Policy spend counter unavailable (Valkey) — cost not tracked")


instance = PolicyEnforcer()
