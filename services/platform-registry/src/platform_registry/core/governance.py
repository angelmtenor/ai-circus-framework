"""
- Title:    Governance hooks for admin mutations — audit row + content-free event
- Author:   Angel Martinez-Tenor

Both features are off by default (`GOVERNANCE_AUDIT_ENABLED`, `GOVERNANCE_EVENTS_ENABLED`);
with them off `record_change` and `publish_change` do nothing and the admin endpoints behave
exactly as before. The flow in a mutating route is:

    pending = record_change(session, ...)   # audit row joins the mutation's transaction
    session.commit()
    publish_change(pending)                 # event only after the change really committed
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import redis
from ai_circus_shared.governance_events import (
    AdminChangeData,
    GovernanceEvents,
    GovernanceEventSettings,
    OutboxDeliverer,
    env_flag,
)
from sqlalchemy.orm import Session

from platform_registry import get_env_config, get_logger
from platform_registry.core.audit import append_audit
from platform_registry.core.models import AuditLog

logger = get_logger(__name__)

# The admin API is one shared bearer key, so the only truthful actor is the credential class.
ACTOR = "admin-api-key"

# Audit action -> governance event name (see ai_circus_shared.governance_events).
EVENT_FOR_ACTION: dict[str, str] = {
    "entitlement.granted": "entitlement.changed",
    "entitlement.revoked": "entitlement.changed",
    "llm_settings.active_model.set": "llm.active_model.changed",
    "voice_settings.active.set": "voice.active_settings.changed",
    "gateway.budget.set": "budget.changed",
    "gateway.budget.deleted": "budget.changed",
}


def _config() -> object:
    """The service config, or an empty object when it cannot load: governance then stays off
    (start-up validation in app.main() is what reports a broken configuration, not a request).
    """
    try:
        return get_env_config()
    except Exception:
        return object()


def audit_enabled() -> bool:
    """Whether admin mutations are appended to the hash-chained audit log (GOVERNANCE_AUDIT_ENABLED)."""
    return env_flag(getattr(_config(), "GOVERNANCE_AUDIT_ENABLED", None))


@lru_cache(maxsize=1)
def get_events() -> GovernanceEvents:
    """The process-wide emitter, built once from config (disabled unless the flag is on)."""
    return GovernanceEvents(GovernanceEventSettings.from_config(_config(), source="/platform-registry"))


@dataclass(frozen=True)
class PendingChange:
    """A change whose audit row (if any) is in the open transaction and whose event is not yet sent."""

    data: AdminChangeData
    tenant_id: str | None
    ai_system_id: str | None


def record_change(
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: str,
    org_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ai_system_id: str | None = None,
) -> PendingChange:
    """Append the audit row (when enabled) to `session`'s open transaction; the caller commits."""
    row: AuditLog | None = None
    if audit_enabled():
        row = append_audit(
            session,
            actor=ACTOR,
            action=action,
            target_type=target_type,
            target_id=target_id,
            org_id=org_id,
            before=before,
            after=after,
        )
    data = AdminChangeData(
        actor=ACTOR,
        action=action,
        target_type=target_type,
        target_id=target_id,
        org_id=org_id,
        before=before,
        after=after,
        audit_id=row.id if row is not None else None,
        audit_hash=row.hash if row is not None else None,
    )
    return PendingChange(data=data, tenant_id=org_id, ai_system_id=ai_system_id)


def publish_change(pending: PendingChange) -> None:
    """Emit the governance event for a committed change (no-op when events are off; never raises)."""
    events = get_events()
    name = EVENT_FOR_ACTION.get(pending.data.action)
    if not events.enabled or name is None:
        return
    events.emit(
        name,
        pending.data,
        tenant_id=pending.tenant_id,
        ai_system_id=pending.ai_system_id,
        subject=pending.data.target_id,
    )


def start_outbox_deliverer() -> OutboxDeliverer | None:
    """Start the webhook deliverer when events are on with the http sink and fully configured.

    Only this long-lived service drains the Valkey outbox; every other service (and Job) just
    appends to it.
    """
    settings = get_events().settings
    if not (settings.enabled and settings.sink == "http"):
        return None
    if not (settings.webhook_url and settings.webhook_secret and settings.cache_url):
        return None  # build_sink already warned and fell back to the log sink
    deliverer = OutboxDeliverer(
        redis.Redis.from_url(settings.cache_url, decode_responses=True),
        url=settings.webhook_url,
        secret=settings.webhook_secret,
    )
    deliverer.start()
    logger.info("Governance event deliverer started (webhook sink)")
    return deliverer
