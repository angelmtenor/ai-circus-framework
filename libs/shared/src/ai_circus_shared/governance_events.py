"""
- Title:    Governance events (CloudEvents 1.0) — content-free evidence for external tools
- Author:   Angel Martinez-Tenor

What happened to an AI system or to the platform that a governance tool would want as
evidence: an entitlement granted or revoked, the active model changed, a budget set, a
model trained. Each is a CloudEvents 1.0 structured-JSON envelope with a reverse-DNS,
versioned `type` (`<namespace>.governance.<name>.v1`) and a closed, schema-published
`data` payload (docs/governance/schemas/).

Rules this module enforces (docs/adr/0011-governance-ready-by-standards.md):
- **Content-free.** `data` carries IDs, numbers and decisions — never a prompt, a
  completion or personal data. The payload models forbid unknown fields, and
  `assert_content_free` additionally rejects content-looking keys and long free text
  inside the open `before`/`after` dicts.
- **Off by default.** `GovernanceEventSettings.enabled` is False until an operator turns
  it on; a disabled emitter does nothing at all.
- **Never blocks, never fails the caller.** `GovernanceEvents.emit` swallows (and logs)
  every error. The `http` sink only does an `XADD` on the request path; a background
  `OutboxDeliverer` does the HTTP, retrying every `retry_after_ms` until the receiver
  acknowledges, so events survive a receiver outage (bounded by `OUTBOX_MAXLEN`).

Sinks, chosen by `GOVERNANCE_EVENT_SINK`: `log` (default — one JSON line on the
`ai_circus.governance.events` logger) or `http` (HMAC-signed webhook through a Valkey
stream outbox). The signature is `X-Governance-Signature: sha256=<hex>` over
`"<X-Governance-Timestamp>.<raw body>"` with the shared secret from the environment.

`llm-gateway` cannot import this package (litellm pins fastapi), so it carries a small
self-contained emitter and a contract test checks it against the same JSON Schemas.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import httpx
import redis
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("ai_circus.governance.events")

DEFAULT_NAMESPACE = "dev.aicircus"
_NAMESPACE_PATTERN = re.compile(r"^[a-z0-9]+(\.[a-z0-9-]+)+$")
OUTBOX_STREAM = "governance:events:outbox"
OUTBOX_GROUP = "governance-deliverers"
OUTBOX_MAXLEN = 10_000
SIGNATURE_HEADER = "X-Governance-Signature"
TIMESTAMP_HEADER = "X-Governance-Timestamp"

# Keys that would mean content or a credential slipped into an event, matched on the
# lower-cased key anywhere inside a payload dict.
FORBIDDEN_KEYS = frozenset(
    {
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
    }
)
_MAX_STRING = 512


class AdminChangeData(BaseModel):
    """`data` of an admin mutation event (entitlement, active model, voice settings, budget)."""

    model_config = ConfigDict(extra="forbid")

    # The credential class that acted (the admin API is a shared bearer key — there is no
    # named user to record), never the key itself.
    actor: str = Field(max_length=64)
    action: str = Field(pattern=r"^[a-z_]+(\.[a-z_]+)+$", max_length=96)
    target_type: str = Field(max_length=64)
    target_id: str = Field(max_length=128)
    org_id: str | None = Field(default=None, max_length=64)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    # Position in the tamper-evident admin audit trail, when it is enabled: lets a receiver
    # anchor the chain externally.
    audit_id: int | None = None
    audit_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ModelTrainedData(BaseModel):
    """`data` of `model.trained`: a tabular model finished training and was stored."""

    model_config = ConfigDict(extra="forbid")

    scenario_slug: str = Field(max_length=64)
    ai_system_id: str = Field(max_length=64)
    org_id: str = Field(max_length=64)
    model_name: str = Field(max_length=128)
    task_type: str | None = Field(default=None, max_length=64)
    selection_metric: str | None = Field(default=None, max_length=64)
    test_score: float | None = None
    training_rows: int | None = None
    metrics: dict[str, float] = Field(default_factory=dict)


# Event name -> data model. The name is the `<name>` in `<namespace>.governance.<name>.v1`.
EVENT_DATA_MODELS: dict[str, type[BaseModel]] = {
    "entitlement.changed": AdminChangeData,
    "llm.active_model.changed": AdminChangeData,
    "voice.active_settings.changed": AdminChangeData,
    "budget.changed": AdminChangeData,
    "model.trained": ModelTrainedData,
}


class CloudEventEnvelope(BaseModel):
    """The CloudEvents 1.0 structured-JSON envelope every governance event uses."""

    model_config = ConfigDict(extra="forbid")

    specversion: Literal["1.0"] = "1.0"
    id: str
    source: str = Field(max_length=128)
    type: str = Field(pattern=r"^[a-z0-9]+(\.[a-z0-9-]+)+\.governance\.[a-z_]+(\.[a-z_]+)*\.v[0-9]+$")
    time: str
    datacontenttype: Literal["application/json"] = "application/json"
    subject: str | None = Field(default=None, max_length=128)
    # CloudEvents extension attributes (lower-case alphanumerics only, by spec).
    tenantid: str | None = Field(default=None, max_length=64)
    aisystemid: str | None = Field(default=None, max_length=64)
    data: dict[str, Any]


def assert_content_free(value: object, _path: str = "data") -> None:
    """Raise ValueError if a payload carries a content-looking key or a long free-text value."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise ValueError(f"{_path}.{key}: key is not allowed in a content-free governance event")
            assert_content_free(item, f"{_path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_content_free(item, f"{_path}[{index}]")
    elif isinstance(value, str) and len(value) > _MAX_STRING:
        raise ValueError(f"{_path}: string longer than {_MAX_STRING} characters is not allowed in a governance event")


def event_type(name: str, namespace: str = DEFAULT_NAMESPACE) -> str:
    """`<namespace>.governance.<name>.v1`; raises on an unknown name or malformed namespace."""
    if name not in EVENT_DATA_MODELS:
        raise ValueError(f"Unknown governance event {name!r}.")
    if not _NAMESPACE_PATTERN.match(namespace):
        raise ValueError(f"Invalid event namespace {namespace!r}: use reverse-DNS, e.g. 'dev.aicircus'.")
    return f"{namespace}.governance.{name}.v1"


def build_event(
    name: str,
    data: BaseModel,
    *,
    source: str,
    namespace: str = DEFAULT_NAMESPACE,
    tenant_id: str | None = None,
    ai_system_id: str | None = None,
    subject: str | None = None,
) -> dict[str, Any]:
    """Validate `data` against the event's model and wrap it in a CloudEvents envelope."""
    expected = EVENT_DATA_MODELS.get(name)
    if expected is None or not isinstance(data, expected):
        raise ValueError(f"Event {name!r} takes {getattr(expected, '__name__', 'no known model')}.")
    payload = data.model_dump(mode="json", exclude_none=True)
    assert_content_free(payload)
    envelope = CloudEventEnvelope(
        id=str(uuid.uuid4()),
        source=source,
        type=event_type(name, namespace),
        time=datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        subject=subject,
        tenantid=tenant_id,
        aisystemid=ai_system_id,
        data=payload,
    )
    return envelope.model_dump(mode="json", exclude_none=True)


def finite_metrics(metrics: dict[str, Any] | None) -> dict[str, float]:
    """The numeric, finite entries of a metrics dict (events carry numbers only)."""
    return {
        str(key): float(value)
        for key, value in (metrics or {}).items()
        if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
    }


def env_flag(value: object) -> bool:
    """A feature flag from an env string ("true"/"1"/"yes"/"on", any case) or a real bool."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass(frozen=True)
class GovernanceEventSettings:
    """Event emission settings; `enabled=False` (the default) makes the emitter a no-op."""

    enabled: bool = False
    sink: Literal["log", "http"] = "log"
    namespace: str = DEFAULT_NAMESPACE
    source: str = "/ai-circus"
    webhook_url: str | None = None
    webhook_secret: str | None = None
    cache_url: str | None = None

    @classmethod
    def from_config(cls, config: object, *, source: str) -> GovernanceEventSettings:
        """Read the `GOVERNANCE_*` fields off a service's EnvConfig; any missing field keeps
        its default, so a service that never declared them simply has events off.
        """

        def _get(name: str, default: Any = None) -> Any:
            value = getattr(config, name, default)
            return value.get_secret_value() if hasattr(value, "get_secret_value") else value

        sink = _get("GOVERNANCE_EVENT_SINK", "log") or "log"
        return cls(
            enabled=env_flag(_get("GOVERNANCE_EVENTS_ENABLED", False)),
            sink="http" if sink == "http" else "log",
            namespace=_get("GOVERNANCE_EVENT_NAMESPACE", DEFAULT_NAMESPACE) or DEFAULT_NAMESPACE,
            source=source,
            webhook_url=_get("GOVERNANCE_WEBHOOK_URL") or None,
            webhook_secret=_get("GOVERNANCE_WEBHOOK_SECRET") or None,
            cache_url=_get("CACHE_URL") or None,
        )


class EventSink(Protocol):
    """Where a built event goes. `send` must not block on the network."""

    def send(self, event: dict[str, Any]) -> None: ...


class LogSink:
    """The default sink: one JSON line per event on the `ai_circus.governance.events` logger.

    Services configure their own logging (loguru, uvicorn) and none of it routes a stdlib
    INFO record to stdout, so the sink installs a bare stdout handler on its own logger the
    first time it is built (once, only if nothing else attached one) — otherwise the default
    sink would silently drop every event.
    """

    def __init__(self) -> None:
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)

    def send(self, event: dict[str, Any]) -> None:
        logger.info("%s", json.dumps(event, separators=(",", ":"), sort_keys=True))


class OutboxSink:
    """Append the event to a Valkey stream; an `OutboxDeliverer` posts it to the webhook."""

    def __init__(self, client: redis.Redis, *, stream: str = OUTBOX_STREAM, maxlen: int = OUTBOX_MAXLEN) -> None:
        self._client = client
        self._stream = stream
        self._maxlen = maxlen

    def send(self, event: dict[str, Any]) -> None:
        self._client.xadd(
            self._stream,
            {"event": json.dumps(event, separators=(",", ":"), sort_keys=True)},
            maxlen=self._maxlen,
            approximate=True,
        )


def sign_body(secret: str, timestamp: str, body: bytes) -> str:
    """`sha256=<hex>` HMAC of `"<timestamp>.<body>"` — what a receiver recomputes to verify."""
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_sink(settings: GovernanceEventSettings) -> EventSink:
    """The configured sink. A misconfigured `http` sink (no URL, secret or Valkey) falls back to
    `log` with a loud warning rather than losing events or failing start-up (fail-open, logged).
    """
    if settings.sink == "http":
        if settings.webhook_url and settings.webhook_secret and settings.cache_url:
            return OutboxSink(redis.Redis.from_url(settings.cache_url, decode_responses=True))
        logger.warning(
            "GOVERNANCE_EVENT_SINK=http needs GOVERNANCE_WEBHOOK_URL, GOVERNANCE_WEBHOOK_SECRET and CACHE_URL — "
            "falling back to the log sink (fail-open)"
        )
    return LogSink()


class GovernanceEvents:
    """The emitter a service holds. Disabled = a no-op; enabled = best-effort, never raises."""

    def __init__(self, settings: GovernanceEventSettings, sink: EventSink | None = None) -> None:
        self.settings = settings
        self._sink: EventSink | None = (sink or build_sink(settings)) if settings.enabled else None

    @property
    def enabled(self) -> bool:
        return self._sink is not None

    def emit(
        self,
        name: str,
        data: BaseModel,
        *,
        tenant_id: str | None = None,
        ai_system_id: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any] | None:
        """Build and send one event; returns it (for tests/callers) or None when off or dropped."""
        if self._sink is None:
            return None
        try:
            event = build_event(
                name,
                data,
                source=self.settings.source,
                namespace=self.settings.namespace,
                tenant_id=tenant_id,
                ai_system_id=ai_system_id,
                subject=subject,
            )
            self._sink.send(event)
        except Exception:  # an evidence side-channel must never fail the request that triggered it
            logger.exception("governance event %r dropped", name)
            return None
        return event


class OutboxDeliverer:
    """Drains the Valkey outbox to the signed webhook, at-least-once.

    A consumer group lets several replicas share the work; an event stays pending (and is
    re-claimed after `retry_after_ms`) until the receiver answers 2xx, so a receiver outage
    only delays delivery. Run `deliver_once` in a loop (`start`) in one long-lived service.
    """

    def __init__(
        self,
        client: redis.Redis,
        *,
        url: str,
        secret: str,
        http: httpx.Client | None = None,
        stream: str = OUTBOX_STREAM,
        group: str = OUTBOX_GROUP,
        consumer: str | None = None,
        retry_after_ms: int = 30_000,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._client = client
        self._url = url
        self._secret = secret
        self._http = http or httpx.Client(timeout=timeout_seconds)
        self._stream = stream
        self._group = group
        self._consumer = consumer or f"c-{uuid.uuid4().hex[:8]}"
        self._retry_after_ms = retry_after_ms
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _ensure_group(self) -> None:
        try:
            self._client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _post(self, raw: str) -> bool:
        body = raw.encode()
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/cloudevents+json",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign_body(self._secret, timestamp, body),
        }
        try:
            response = self._http.post(self._url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("governance webhook unreachable (%s) — will retry", type(exc).__name__)
            return False
        if not response.is_success:
            logger.warning("governance webhook answered %s — will retry", response.status_code)
            return False
        return True

    def deliver_once(self, *, count: int = 50) -> int:
        """Try the pending (previously failed) events, then the new ones; returns how many were acknowledged."""
        self._ensure_group()
        delivered = 0
        entries: list[tuple[str, dict[str, str]]] = []
        claimed = self._client.xautoclaim(
            self._stream, self._group, self._consumer, min_idle_time=self._retry_after_ms, start_id="0-0", count=count
        )
        entries.extend(claimed[1])
        fresh = self._client.xreadgroup(self._group, self._consumer, {self._stream: ">"}, count=count)
        for _stream, messages in fresh or []:
            entries.extend(messages)
        for entry_id, fields in entries:
            if self._post(fields["event"]):
                self._client.xack(self._stream, self._group, entry_id)
                self._client.xdel(self._stream, entry_id)
                delivered += 1
        return delivered

    def start(self, interval_seconds: float = 2.0, on_error: Callable[[Exception], None] | None = None) -> None:
        """Run `deliver_once` every `interval_seconds` on a daemon thread (idempotent)."""
        if self._thread is not None:
            return

        def _loop() -> None:
            while not self._stop.wait(interval_seconds):
                try:
                    self.deliver_once()
                except Exception as exc:  # Valkey blips must not kill the deliverer
                    (on_error or (lambda e: logger.warning("governance outbox pass failed: %s", e)))(exc)

        self._thread = threading.Thread(target=_loop, name="governance-outbox", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
