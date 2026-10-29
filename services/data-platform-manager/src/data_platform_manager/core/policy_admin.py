"""
- Title:    Policy cache invalidation / overwrite for the gateway's decision cache
- Author:   Angel Martinez-Tenor

llm-gateway caches each policy decision in Valkey at `tenant-{org}:policy:{ai_system}` (a hash:
field = model, `*` = any model — see llm_gateway.policy_hook). An external governance tool that
changes its mind (suspends an AI system, lifts a restriction) should not have to wait out the TTL
or be polled, so it calls `POST /governance/policy/invalidate`:

- without a `decision` the cached hash is deleted and the next call asks the decision point again;
- with a `decision` the hash is replaced by that decision for every model (`*`), for
  `ttl_seconds`, so a suspension takes effect on the very next call.

The call is authenticated by an HMAC over `"<timestamp>.<raw body>"` with a secret shared out of
band (`GOVERNANCE_POLICY_HMAC_SECRET`) — the same scheme the event webhook uses in the other
direction — and a timestamp window against replay of stale requests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from functools import lru_cache
from typing import Any

import redis
from ai_circus_shared.cache import TenantCache
from ai_circus_shared.governance_events import GovernanceEvents, GovernanceEventSettings
from ai_circus_shared.governance_policy import PolicyDecision
from pydantic import BaseModel, ConfigDict, Field

from data_platform_manager import get_env_config

MAX_BODY_BYTES = 16 * 1024
MAX_SKEW_SECONDS = 300
DEFAULT_PUSH_TTL_SECONDS = 3600


class PolicyInvalidateIn(BaseModel):
    """Body of `POST /governance/policy/invalidate`."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    ai_system_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    decision: PolicyDecision | None = None
    ttl_seconds: int | None = Field(default=None, ge=1, le=86_400)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """`sha256=<hex>` of HMAC-SHA256(secret, "<timestamp>.<body>")."""
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def signature_is_valid(
    secret: str, *, timestamp: str | None, signature: str | None, body: bytes, now: float | None = None
) -> bool:
    """Constant-time check of the signature, rejecting timestamps outside the allowed window."""
    if not timestamp or not signature or not timestamp.isdigit():
        return False
    if abs((now if now is not None else time.time()) - int(timestamp)) > MAX_SKEW_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def apply_invalidation(cache: TenantCache, request: PolicyInvalidateIn, *, default_ttl: int) -> bool:
    """Delete the cached decisions, or overwrite them with the pushed one. Returns True if overwritten."""
    client: redis.Redis = cache._client
    key = cache.key(request.tenant_id, f"policy:{request.ai_system_id}")
    client.delete(key)
    if request.decision is None:
        return False
    payload = json.dumps(request.decision.model_dump(mode="json"), separators=(",", ":"))
    pipe = client.pipeline()
    pipe.hset(key, "*", payload)
    pipe.expire(key, request.ttl_seconds or default_ttl)
    pipe.execute()
    return True


def audit_summary(request: PolicyInvalidateIn) -> dict[str, Any] | None:
    """What the audit trail records about a pushed decision: its outcome, never anything else."""
    if request.decision is None:
        return None
    decision = request.decision
    return {
        "decision": decision.decision,
        "reason_code": decision.reason_code,
        "status": decision.obligations.status,
        "policy_version": decision.policy_version,
    }


@lru_cache(maxsize=1)
def get_events() -> GovernanceEvents:
    """The process-wide event emitter (disabled unless GOVERNANCE_EVENTS_ENABLED is true)."""
    try:
        config: object = get_env_config()
    except Exception:  # config invalid: start-up validation reports it, not a request
        config = object()
    return GovernanceEvents(GovernanceEventSettings.from_config(config, source="/data-platform-manager"))
