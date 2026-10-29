"""
- Title:    Hash-chained admin audit log (database side)
- Author:   Angel Martinez-Tenor

platform-registry is the single writer of `audit_log`. `append_audit` runs inside the
caller's transaction — the mutation and its audit row commit or roll back together — and
serialises concurrent writers with a Postgres transaction-level advisory lock, so the
"previous hash" it reads is always the real tail of the chain. The pure chain logic
(hash, verification) is `ai_circus_shared.governance_audit`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ai_circus_shared.governance_audit import GENESIS_HASH, AuditRecord, AuditVerification, entry_hash, verify_chain
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from platform_registry.core.models import AuditLog

# Arbitrary constant namespacing this table's advisory lock.
_ADVISORY_LOCK_KEY = 7_340_001


def _normalised(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """JSON round-trip so what is hashed is exactly what the JSON column will return."""
    return None if value is None else json.loads(json.dumps(value, sort_keys=True))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def append_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    org_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    """Add one chained row to `session` (flushed, not committed — the caller commits)."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADVISORY_LOCK_KEY})
    tail = session.scalars(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)).first()
    prev_hash = tail or GENESIS_HASH
    content = {
        "ts": _now(),
        "actor": actor,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "org_id": org_id,
        "before": _normalised(before),
        "after": _normalised(after),
    }
    row = AuditLog(prev_hash=prev_hash, hash=entry_hash(prev_hash, **content), **content)
    session.add(row)
    session.flush()
    return row


def list_audit(
    session: Session, *, limit: int = 100, before_id: int | None = None, org_id: str | None = None
) -> list[AuditLog]:
    """Newest first; `before_id` pages backwards, `org_id` filters to one tenant's actions."""
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if before_id is not None:
        stmt = stmt.where(AuditLog.id < before_id)
    if org_id is not None:
        stmt = stmt.where(AuditLog.org_id == org_id)
    return list(session.scalars(stmt))


def verify_audit(session: Session, *, batch_size: int = 1000) -> AuditVerification:
    """Walk the whole table in id order (streamed in batches) and check the chain."""

    def _records() -> Any:
        last_id = 0
        while True:
            rows = session.scalars(
                select(AuditLog).where(AuditLog.id > last_id).order_by(AuditLog.id).limit(batch_size)
            ).all()
            if not rows:
                return
            for row in rows:
                yield AuditRecord.model_validate(row)
            last_id = rows[-1].id

    return verify_chain(_records())
