"""
- Title:    Governance overview — what is switched on, which AI systems are declared, is the audit trail intact
- Author:   Angel Martinez-Tenor

Read-only and content-free: it summarises switches, the scenarios' *declared* governance metadata
(never inferred) and the audit trail's chain state, so the admin Platform page can show the
governance posture at a glance. It answers even while every feature is off — that is its job — and
never changes anything. It supports a governance programme; it does not make a deployment compliant.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ai_circus_shared.governance_audit import AuditVerification
from ai_circus_shared.governance_events import env_flag
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from platform_registry.core import audit
from platform_registry.core.models import AuditLog, Scenario

RECENT_AUDIT_ENTRIES = 10
UNDECLARED = "undeclared"


class Switch(BaseModel):
    """One governance feature flag of this service and whether it is on."""

    env: str
    label: str
    enabled: bool


class AiSystemRow(BaseModel):
    """One scenario as an AI system, with only what its owner declared."""

    slug: str
    ai_system_id: str
    kind: str
    title: str
    declared: bool
    owner: str | None = None
    risk_category: str = "unassessed"
    oversight: str | None = None
    personal_data: bool | None = None


class AiSystemsSummary(BaseModel):
    """Counts over every scenario; `unassessed` / `undeclared` mean nobody has decided yet."""

    total: int
    declared: int
    by_risk: dict[str, int]
    by_oversight: dict[str, int]
    items: list[AiSystemRow]


class AuditEntrySummary(BaseModel):
    """One recent audit row, without its before/after payloads."""

    id: int
    ts: str
    actor: str
    action: str
    target_type: str
    target_id: str
    org_id: str | None = None


class AuditSummary(BaseModel):
    """Audit trail state; `verification` is None while the trail is switched off."""

    enabled: bool
    entries: int = 0
    verification: AuditVerification | None = None
    recent: list[AuditEntrySummary] = []


class GovernanceOverview(BaseModel):
    """Everything the admin Platform → Governance tab shows."""

    switches: list[Switch]
    event_sink: str | None
    inventory_path: str | None
    ai_systems: AiSystemsSummary
    audit: AuditSummary


SWITCHES: tuple[tuple[str, str], ...] = (
    ("GOVERNANCE_EVENTS_ENABLED", "Content-free governance events (CloudEvents)"),
    ("GOVERNANCE_AUDIT_ENABLED", "Hash-chained admin audit trail"),
    ("GOVERNANCE_INVENTORY_ENABLED", "AI-BOM inventory export (CycloneDX 1.6)"),
    ("GOVERNANCE_CATALOG_ENABLED", "Declared risk published to the gateway (by_risk fail mode)"),
)


def _ai_systems(session: Session) -> AiSystemsSummary:
    rows: list[AiSystemRow] = []
    for scenario in session.scalars(select(Scenario).order_by(Scenario.slug)):
        record: dict[str, Any] = scenario.governance or {}
        rows.append(
            AiSystemRow(
                slug=scenario.slug,
                ai_system_id=str(record.get("ai_system_id") or scenario.slug),
                kind=scenario.kind,
                title=scenario.title,
                declared=bool(record.get("declared")),
                owner=record.get("owner"),
                risk_category=str((record.get("ai_act") or {}).get("risk_category") or "unassessed"),
                oversight=(record.get("human_oversight") or {}).get("mode"),
                personal_data=(record.get("data") or {}).get("personal_data"),
            )
        )
    return AiSystemsSummary(
        total=len(rows),
        declared=sum(row.declared for row in rows),
        by_risk=dict(Counter(row.risk_category for row in rows)),
        by_oversight=dict(Counter(row.oversight or UNDECLARED for row in rows)),
        items=rows,
    )


def _audit_summary(session: Session, *, enabled: bool) -> AuditSummary:
    if not enabled:
        return AuditSummary(enabled=False)
    recent = [
        AuditEntrySummary.model_validate(row, from_attributes=True)
        for row in audit.list_audit(session, limit=RECENT_AUDIT_ENTRIES)
    ]
    return AuditSummary(
        enabled=True,
        entries=session.scalar(select(func.count()).select_from(AuditLog)) or 0,
        verification=audit.verify_audit(session),
        recent=recent,
    )


def build_overview(session: Session, config: object, *, audit_enabled: bool) -> GovernanceOverview:
    """The overview for this service's current configuration and database."""
    switches = [Switch(env=env, label=label, enabled=env_flag(getattr(config, env, None))) for env, label in SWITCHES]
    inventory_on = any(s.enabled for s in switches if s.env == "GOVERNANCE_INVENTORY_ENABLED")
    events_on = any(s.enabled for s in switches if s.env == "GOVERNANCE_EVENTS_ENABLED")
    return GovernanceOverview(
        switches=switches,
        event_sink=(getattr(config, "GOVERNANCE_EVENT_SINK", None) or "log") if events_on else None,
        inventory_path="/governance/inventory" if inventory_on else None,
        ai_systems=_ai_systems(session),
        audit=_audit_summary(session, enabled=audit_enabled),
    )
