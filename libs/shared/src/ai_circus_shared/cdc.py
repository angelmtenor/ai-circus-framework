"""
- Title:    Change-data-capture via Postgres logical replication
- Author:   ai-circus-framework contributors

Reads row-level changes off a Postgres logical replication slot using the
`test_decoding` output plugin — built into every stock Postgres image (unlike
wal2json, a third-party extension this repo deliberately avoids adding, since
it would mean maintaining a custom Postgres image just for this). The only
prerequisite is `wal_level=logical` (see docker-compose.yml's postgres
service) and a slot; both `ensure_slot`/`poll_changes` below use plain SQL
(`pg_create_logical_replication_slot`/`pg_logical_slot_get_changes`), so any
of this repo's existing psycopg3/SQLAlchemy sessions can call them — no
second Postgres driver needed (psycopg3 has no *streaming*-replication-
protocol client, unlike psycopg2, but that protocol isn't required for this
polling-based approach).

`test_decoding`'s output is line-oriented plain text, not JSON — `parse_change`
turns one "table SCHEMA.TABLE: OP: col[type]:'value' ..." line into a
structured `ChangeEvent`; BEGIN/COMMIT transaction-boundary lines parse to
`None` and are filtered out by `poll_changes`. This is good enough to
demonstrate a real change feed, not a byte-for-byte-faithful decoder: a
literal single quote embedded inside a text/json column's own value will not
round-trip correctly (a production CDC pipeline would use wal2json or the
binary pgoutput protocol instead of test_decoding's debug-oriented text
format). DELETE rows only carry the table's REPLICA IDENTITY columns (the
primary key, by Postgres's own default) — not the full deleted row — same
limitation any logical-decoding consumer has unless REPLICA IDENTITY FULL is
set on the source table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

_CHANGE_LINE = re.compile(r"^table (?P<schema>\S+)\.(?P<table>\S+): (?P<op>INSERT|UPDATE|DELETE): (?P<cols>.*)$")
_COLUMN = re.compile(r"(?P<name>\S+?)\[(?P<type>[^\]]+)\]:(?:'(?P<quoted>(?:[^']|'')*)'|(?P<bare>\S+))")


@dataclass(frozen=True)
class ChangeEvent:
    """One row-level change captured off a logical replication slot."""

    schema: str
    table: str
    operation: str  # "INSERT" | "UPDATE" | "DELETE"
    columns: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form, for publishing via ai_circus_shared.events."""
        return {"schema": self.schema, "table": self.table, "operation": self.operation, "columns": self.columns}


def parse_change(line: str) -> ChangeEvent | None:
    """Parse one test_decoding output line into a ChangeEvent, or None for a
    BEGIN/COMMIT transaction-boundary line (or anything else unrecognized).
    """
    match = _CHANGE_LINE.match(line)
    if match is None:
        return None
    columns: dict[str, str] = {}
    for col in _COLUMN.finditer(match.group("cols")):
        quoted = col.group("quoted")
        value = quoted.replace("''", "'") if quoted is not None else col.group("bare")
        columns[col.group("name")] = value
    return ChangeEvent(
        schema=match.group("schema"), table=match.group("table"), operation=match.group("op"), columns=columns
    )


def ensure_slot(session: Session, slot_name: str) -> bool:
    """Create the logical replication slot (test_decoding plugin) if it
    doesn't already exist. Returns True if it was just created, False if it
    already existed. Idempotent — safe to call before every poll.
    """
    exists = session.execute(
        text("SELECT 1 FROM pg_replication_slots WHERE slot_name = :name"), {"name": slot_name}
    ).first()
    if exists is not None:
        return False
    session.execute(text("SELECT pg_create_logical_replication_slot(:name, 'test_decoding')"), {"name": slot_name})
    session.commit()
    return True


def poll_changes(session: Session, slot_name: str) -> list[ChangeEvent]:
    """Fetch and consume every change since the last poll — this ADVANCES the
    slot's position, so call it periodically/on-demand rather than
    speculatively; each change is returned exactly once, across all callers
    (a replication slot has no concept of independent consumer groups the way
    a Kafka topic does).
    """
    rows = session.execute(
        text("SELECT data FROM pg_logical_slot_get_changes(:name, NULL, NULL)"), {"name": slot_name}
    ).all()
    parsed = (parse_change(row[0]) for row in rows)
    return [event for event in parsed if event is not None]


def slot_status(session: Session, slot_name: str) -> dict[str, Any] | None:
    """Current position/activity of the slot, or None if it doesn't exist yet."""
    row = session.execute(
        text("SELECT active, confirmed_flush_lsn::text FROM pg_replication_slots WHERE slot_name = :name"),
        {"name": slot_name},
    ).first()
    if row is None:
        return None
    return {"active": row[0], "confirmed_flush_lsn": row[1]}
