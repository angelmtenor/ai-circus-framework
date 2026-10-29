"""
- Title:    Governance events emitted by llm-gateway (self-contained CloudEvents emitter)
- Author:   Angel Martinez-Tenor

llm-gateway cannot import `ai_circus_shared` (litellm pins `fastapi` exactly, see
pyproject.toml and budget_hook.py), so this is a small standalone twin of
`ai_circus_shared.governance_events` for the two events the gateway raises:
`policy.decision` (a call was denied, degraded, or let through because the decision point was
unreachable) and `budget.exhausted`. The envelope, the type naming and the outbox stream are the
same, and a contract test validates what this emits against the published JSON Schemas in
docs/governance/schemas/.

Content-free by construction: each event takes a closed set of keys (ids, decision, reason code,
numbers) — never a prompt, completion or token. Off unless `GOVERNANCE_EVENTS_ENABLED` is true;
`emit` never raises and never blocks on the network (the `http` sink only appends to the Valkey
outbox that platform-registry delivers to the webhook).
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("ai_circus.governance.events")

DEFAULT_NAMESPACE = "dev.aicircus"
OUTBOX_STREAM = "governance:events:outbox"
OUTBOX_MAXLEN = 10_000
_MAX_STRING = 512

_FORBIDDEN_KEYS = frozenset({
    "prompt",
    "completion",
    "messages",
    "message",
    "content",
    "text",
    "input",
    "output",
    "response",
    "email",
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
})
# Event name -> the only keys its `data` may carry (mirrors the published schemas).
_EVENT_KEYS: dict[str, frozenset[str]] = {
    "policy.decision": frozenset({
        "tenant_id",
        "ai_system_id",
        "model",
        "call_type",
        "decision",
        "reason_code",
        "policy_version",
        "fail_open",
        "served_model",
        "max_tokens",
    }),
    "budget.exhausted": frozenset({"org_id", "period", "spent_usd", "cap_usd", "scope", "ai_system_id"}),
}


def env_flag(value: object) -> bool:
    """A feature flag from an env string ("true"/"1"/"yes"/"on", any case) or a real bool."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def build_event(
    name: str,
    data: dict[str, Any],
    *,
    namespace: str = DEFAULT_NAMESPACE,
    source: str = "/llm-gateway",
    tenant_id: str | None = None,
    ai_system_id: str | None = None,
    subject: str | None = None,
) -> dict[str, Any]:
    """A CloudEvents 1.0 structured-JSON envelope around `data`; raises ValueError on a bad payload."""
    allowed = _EVENT_KEYS.get(name)
    if allowed is None:
        raise ValueError(f"Unknown governance event {name!r}.")
    payload = {key: value for key, value in data.items() if value is not None}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"{name}: keys {sorted(unknown)} are not part of the event schema")
    for key, value in payload.items():
        if key.lower() in _FORBIDDEN_KEYS:
            raise ValueError(f"{name}.{key}: not allowed in a content-free governance event")
        if isinstance(value, str) and len(value) > _MAX_STRING:
            raise ValueError(f"{name}.{key}: string too long for a governance event")
    event: dict[str, Any] = {
        "specversion": "1.0",
        "id": str(uuid.uuid4()),
        "source": source,
        "type": f"{namespace}.governance.{name}.v1",
        "time": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "datacontenttype": "application/json",
        "data": payload,
    }
    if subject:
        event["subject"] = subject
    if tenant_id:
        event["tenantid"] = tenant_id
    if ai_system_id:
        event["aisystemid"] = ai_system_id
    return event


class GatewayEvents:
    """The gateway's emitter. Disabled = a no-op; enabled = best-effort, never raises."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        sink: str = "log",
        namespace: str = DEFAULT_NAMESPACE,
        client: Any = None,
    ) -> None:
        """Configure the emitter; `client` is a sync redis client used only by the http sink."""
        self.enabled = enabled
        self._sink = sink if sink in {"log", "http"} else "log"
        self._namespace = namespace or DEFAULT_NAMESPACE
        self._client = client  # sync redis client, only used by the http sink
        if enabled and not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        if enabled and (logger.level == logging.NOTSET or logger.level > logging.INFO):
            logger.setLevel(logging.INFO)

    @classmethod
    def from_config(cls, config: Any, client_factory: Any = None) -> GatewayEvents:
        """Build from the gateway's EnvConfig (any missing `GOVERNANCE_*` field = events off)."""
        enabled = env_flag(getattr(config, "GOVERNANCE_EVENTS_ENABLED", None))
        sink = getattr(config, "GOVERNANCE_EVENT_SINK", None) or "log"
        client = client_factory() if (enabled and sink == "http" and client_factory is not None) else None
        return cls(
            enabled=enabled,
            sink=sink,
            namespace=getattr(config, "GOVERNANCE_EVENT_NAMESPACE", None) or DEFAULT_NAMESPACE,
            client=client,
        )

    def emit(
        self,
        name: str,
        data: dict[str, Any],
        *,
        tenant_id: str | None = None,
        ai_system_id: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any] | None:
        """Build and send one event; returns it, or None when off or dropped."""
        if not self.enabled:
            return None
        try:
            event = build_event(
                name,
                data,
                namespace=self._namespace,
                tenant_id=tenant_id,
                ai_system_id=ai_system_id,
                subject=subject,
            )
            line = json.dumps(event, separators=(",", ":"), sort_keys=True)
            if self._sink == "http" and self._client is not None:
                self._client.xadd(OUTBOX_STREAM, {"event": line}, maxlen=OUTBOX_MAXLEN, approximate=True)
            else:
                logger.info("%s", line)
        except Exception:  # an evidence side-channel must never fail the request that triggered it
            logger.exception("governance event %r dropped", name)
            return None
        return event
