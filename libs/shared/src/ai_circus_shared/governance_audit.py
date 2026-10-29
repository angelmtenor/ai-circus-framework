"""
- Title:    Tamper-evident admin audit trail — record shape, hash chain, verification
- Author:   Angel Martinez-Tenor

Every admin mutation (entitlements, the active LLM / voice provider, gateway budgets…) is
appended to an `audit_log` table by platform-registry, the single writer. Each row stores
the SHA-256 of the previous row, so editing, inserting or deleting a row anywhere but the
very end breaks every hash after it and `verify_chain` reports the first broken row. The
newest hash is also carried in the matching governance event (`audit_hash`), so a
receiver that keeps it can detect even a truncated tail.

This module is pure (no database): the row shape (`AuditRecord`), the canonical hash and
the verification walk. platform-registry owns the SQL and the write lock.

Limits worth stating: the chain proves the log was not altered after the fact by someone
who cannot recompute it — an operator with full database write access can rewrite the whole
chain, which is what the externally anchored `audit_hash` in events is for. The admin API is
a shared bearer key, so the recorded `actor` is the credential class, not a named person.
Records carry IDs, settings values and decisions — never prompts, completions or personal data.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

GENESIS_HASH = "0" * 64


class AuditRecord(BaseModel):
    """One audit row as stored and served (`GET /governance/audit`)."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    ts: str = Field(description="UTC timestamp, ISO-8601 with milliseconds, e.g. 2026-10-29T10:15:00.123Z")
    actor: str = Field(max_length=64)
    action: str = Field(max_length=96)
    target_type: str = Field(max_length=64)
    target_id: str = Field(max_length=128)
    org_id: str | None = Field(default=None, max_length=64)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    prev_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AuditVerification(BaseModel):
    """Outcome of walking the chain."""

    ok: bool
    checked: int
    first_invalid_id: int | None = None
    reason: str | None = None


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def entry_hash(
    prev_hash: str,
    *,
    ts: str,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    org_id: str | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> str:
    """SHA-256 over the previous hash and the canonical JSON of the row's content (not its id,
    which the database assigns; the chain order is what the hashes protect).
    """
    payload = {
        "ts": ts,
        "actor": actor,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "org_id": org_id,
        "before": before,
        "after": after,
    }
    return hashlib.sha256(prev_hash.encode() + b"\n" + _canonical(payload)).hexdigest()


def record_hash(record: AuditRecord) -> str:
    """The hash `record` should carry, recomputed from its content and its `prev_hash`."""
    return entry_hash(
        record.prev_hash,
        ts=record.ts,
        actor=record.actor,
        action=record.action,
        target_type=record.target_type,
        target_id=record.target_id,
        org_id=record.org_id,
        before=record.before,
        after=record.after,
    )


def verify_chain(records: Iterable[AuditRecord]) -> AuditVerification:
    """Walk `records` in id order; the first row whose stored hash, or link to its predecessor,
    does not recompute is reported. An empty log is trivially valid.
    """
    expected_prev = GENESIS_HASH
    checked = 0
    for record in records:
        if record.prev_hash != expected_prev:
            return AuditVerification(
                ok=False,
                checked=checked,
                first_invalid_id=record.id,
                reason="prev_hash does not match the previous row (row removed, inserted or reordered)",
            )
        if record.hash != record_hash(record):
            return AuditVerification(
                ok=False, checked=checked, first_invalid_id=record.id, reason="row content does not match its hash"
            )
        expected_prev = record.hash
        checked += 1
    return AuditVerification(ok=True, checked=checked)
