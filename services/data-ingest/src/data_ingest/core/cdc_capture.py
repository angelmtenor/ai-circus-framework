"""
- Title:    Operational source database → bronze by change data capture
- Author:   Angel Martinez-Tenor

Drains one logical replication slot (test_decoding, see ai_circus_shared.cdc) on the sources
database every few seconds. Each change to a table a data product declares under a `cdc` source
(schema = the data product's slug) is appended to `bronze_cdc_<table>` — the ordered change log
dbt turns into SCD2 dimensions — and applied to `cdc_<table>`, the current state the contract
rules look values up in. A freshly created slot (or an empty current-state table over a
populated source table) starts with a snapshot, so history captured before the slot existed
isn't lost.

The slot retains WAL while nothing drains it: Postgres runs with `max_slot_wal_keep_size`, and
`make k3s-data-core-down` drops the slot.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from ai_circus_shared.cdc import parse_change
from sqlalchemy import Connection, Engine, text

from data_ingest.core.logger import get_logger
from data_ingest.core.pipeline_events import PipelineEvents
from data_ingest.core.products import DataProduct

logger = get_logger(__name__)

SLOT = "data_ingest"
MAX_CHANGES_PER_POLL = 5000


def _value(raw: str) -> str | None:
    return None if raw == "null" else raw


class CdcWorker:
    """Drains the sources database's replication slot into each data product's CDC tables."""

    def __init__(
        self,
        sources_engine: Engine,
        warehouse: Engine,
        products: dict[str, DataProduct],
        events: PipelineEvents,
        interval_seconds: float,
    ) -> None:
        """Index the declared CDC tables by (schema, table)."""
        self._sources, self._warehouse, self._events = sources_engine, warehouse, events
        self._interval = interval_seconds
        # (schema, table) -> (product, key column)
        self._tables = {
            (p.schema, t.name): (p, t.key) for p in products.values() for s in p.config.cdc_sources() for t in s.tables
        }
        self._stop = threading.Event()
        self.captured = 0
        self.last_poll: str | None = None

    def stop(self) -> None:
        """Ask the worker loop to exit after its current poll."""
        self._stop.set()

    def run(self) -> None:
        """Ensure the slot, snapshot if needed, then poll every interval until stopped."""
        if not self._tables:
            return
        created = self._ensure_slot()
        self._snapshot(force=created)
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:
                logger.exception("CDC poll failed")
            self._stop.wait(self._interval)

    def _ensure_slot(self) -> bool:
        with self._sources.begin() as conn:
            row = conn.execute(
                text("SELECT wal_status FROM pg_replication_slots WHERE slot_name = :s"), {"s": SLOT}
            ).first()
            if row is not None and row[0] != "lost":
                return False
            if row is not None:  # invalidated by max_slot_wal_keep_size while nobody drained it
                logger.warning("CDC slot {} was invalidated; recreating it and re-snapshotting", SLOT)
                conn.execute(text("SELECT pg_drop_replication_slot(:s)"), {"s": SLOT})
            conn.execute(text("SELECT pg_create_logical_replication_slot(:s, 'test_decoding')"), {"s": SLOT})
            logger.info("Created CDC replication slot {}", SLOT)
            return True

    def _snapshot(self, *, force: bool) -> None:
        for (schema, table), (product, key) in self._tables.items():
            with self._sources.connect() as src:
                exists = src.execute(text("SELECT to_regclass(:t)"), {"t": f'"{schema}"."{table}"'}).scalar()
                if exists is None:
                    continue
                with self._warehouse.begin() as wh:
                    empty = wh.execute(text(f'SELECT NOT EXISTS (SELECT 1 FROM "{schema}"."cdc_{table}")')).scalar()
                    if not (force or empty):
                        continue
                    rows = src.execute(text(f'SELECT to_jsonb(t) FROM "{schema}"."{table}" t')).scalars().all()
                    self._apply(wh, product, table, key, [("SNAPSHOT", row, None) for row in rows])
                    logger.info("CDC snapshot of {}.{}: {} rows", schema, table, len(rows))

    def poll(self) -> int:
        """Consume the slot's pending changes into bronze; returns how many were declared-table changes."""
        with self._sources.begin() as conn:
            rows = conn.execute(
                text("SELECT lsn::text, data FROM pg_logical_slot_get_changes(:s, NULL, :n)"),
                {"s": SLOT, "n": MAX_CHANGES_PER_POLL},
            ).all()
            changes: dict[tuple[str, str], list[tuple[str, dict[str, Any], str | None]]] = {}
            for lsn, data in rows:
                change = parse_change(data)
                if change is None or (change.schema, change.table) not in self._tables:
                    continue
                row = {name: _value(value) for name, value in change.columns.items()}
                changes.setdefault((change.schema, change.table), []).append((change.operation, row, lsn))
            # Apply inside the same transaction that consumed the slot: if the warehouse write
            # fails, the slot isn't advanced and the changes come back on the next poll.
            with self._warehouse.begin() as wh:
                for (schema, table), items in changes.items():
                    product, key = self._tables[schema, table]
                    self._apply(wh, product, table, key, items)
        count = sum(len(v) for v in changes.values())
        self.captured += count
        return count

    def _apply(
        self,
        wh: Connection,
        product: DataProduct,
        table: str,
        key: str,
        items: list[tuple[str, dict[str, Any], str | None]],
    ) -> None:
        records = [(op, row, lsn) for op, row, lsn in items if row.get("org_id") and row.get(key) is not None]
        if not records:
            return
        schema = product.schema
        wh.execute(
            text(f"""INSERT INTO "{schema}"."bronze_cdc_{table}" (org_id, key, op, row, lsn)
                     VALUES (:org_id, :key, :op, CAST(:row AS jsonb), :lsn)"""),
            [
                {
                    "org_id": row["org_id"],
                    "key": str(row[key]),
                    "op": op,
                    "row": json.dumps(row, default=str),
                    "lsn": lsn,
                }
                for op, row, lsn in records
            ],
        )
        latest: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        for op, row, _ in records:
            latest[row["org_id"], str(row[key])] = (op, row)
        wh.execute(
            text(f"""INSERT INTO "{schema}"."cdc_{table}" (org_id, key, row, op, changed_at)
                     VALUES (:org_id, :key, CAST(:row AS jsonb), :op, now())
                     ON CONFLICT (org_id, key) DO UPDATE SET
                         row = CASE WHEN EXCLUDED.op = 'DELETE' THEN "cdc_{table}".row ELSE EXCLUDED.row END,
                         op = EXCLUDED.op, changed_at = now()"""),
            [
                {"org_id": org, "key": k, "row": json.dumps(row, default=str), "op": op}
                for (org, k), (op, row) in latest.items()
            ],
        )
        by_org: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for op, row, _ in records:
            by_org.setdefault(row["org_id"], []).append((op, row))
        for org, changes in by_org.items():
            if len(changes) <= 5:
                for op, row in changes:
                    for stage in ("validated", "bronze"):
                        self._events.record(
                            org,
                            product.slug,
                            stage,
                            "ok",
                            entity=table,
                            record_id=str(row[key]),
                            source="cdc",
                            detail={"op": op},
                        )
            else:
                self._events.batch(org, product.slug, "bronze", "ok", len(changes), {"table": table, "source": "cdc"})
        self._events.flush()
