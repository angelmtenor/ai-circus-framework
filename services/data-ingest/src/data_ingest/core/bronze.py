"""
- Title:    Contract check → bronze upsert / quarantine → hot-path increments
- Author:   Angel Martinez-Tenor

The one path every record takes, whether it arrived on a Kafka topic or in a landing file
(docs/adr/0004):

1. `classify` validates it against its source's contract (JSON Schema + record rules) and
   extracts business key, version, event time, the row-filter value and the search text;
2. `upsert_bronze` writes the accepted ones by (org_id, business key, version) in one
   statement: a new key is an insert, the same key with the same payload hash is a no-op (a
   re-send), the same key with a different hash is a counted correction;
3. `insert_quarantine` keeps the rejected ones with every violation;
4. `hot_increments`/`apply_hot` add the *new* keys (never a re-send or a correction, and for a
   versioned source only its first version) to the per-minute live aggregates.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ai_circus_shared.contracts import get_one, get_path, parse_timestamp
from ai_circus_shared.data_product import KafkaSource, bronze_table, hot_table
from sqlalchemy import Connection, Engine, text

from data_ingest.core.products import DataProduct

MAX_SEARCH_TEXT = 2000


@dataclass(frozen=True)
class Accepted:
    """A record that passed its contract, ready for bronze."""

    org_id: str
    key: str
    version: int
    payload: dict[str, Any]
    payload_hash: str
    event_ts: str | None
    filter_value: str | None
    search_text: str


@dataclass(frozen=True)
class Rejected:
    """A record that failed its contract, with every violation, bound for quarantine."""

    org_id: str
    key: str | None
    payload: Any
    raw: str | None
    violations: list[dict[str, str]]
    filter_value: str | None


def payload_hash(payload: Any) -> str:
    """SHA-256 of the canonical JSON (sorted keys): equal payloads hash equal whatever their key order."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def classify(
    product: DataProduct,
    source: KafkaSource,
    org_id: str,
    payload: Any,
    *,
    raw: str | None = None,
    lookup: Callable[[str, str, Any], bool] | None = None,
) -> Accepted | Rejected:
    """Validate one decoded record. `payload` is None when it wasn't valid JSON (`raw` kept)."""
    row_filter = product.config.access.row_filter
    filter_value = _text(get_one(payload, row_filter.column)) if row_filter and isinstance(payload, dict) else None
    if not isinstance(payload, dict):
        reason = "not a JSON object" if payload is not None else "not valid JSON"
        return Rejected(org_id, None, payload, raw, [{"rule": "schema", "message": reason}], filter_value)
    key = _text(get_one(payload, source.business_key))
    violations = [v.to_dict() for v in product.contracts[source.id].check(payload, lookup=lookup)]
    if key is None and not violations:
        violations = [{"rule": "schema", "message": f"missing business key {source.business_key}"}]
    if violations:
        return Rejected(org_id, key, payload, None, violations, filter_value)
    version = int(get_one(payload, source.version_field, 1)) if source.version_field else 1
    record = next((r for r in product.config.records if r.source == source.id), None)
    search = " ".join(str(v) for path in (record.search if record else []) for v in get_path(payload, path) if v)
    return Accepted(
        org_id=org_id,
        key=key or "",
        version=version,
        payload=payload,
        payload_hash=payload_hash(payload),
        event_ts=_text(get_one(payload, source.event_time)),
        filter_value=filter_value,
        search_text=search[:MAX_SEARCH_TEXT],
    )


@dataclass
class UpsertResult:
    """What one bronze upsert did: the new keys, how many corrections, how many identical re-sends."""

    inserted: list[Accepted]
    corrected: int
    duplicates: int


def upsert_bronze(
    conn: Connection,
    product: DataProduct,
    source: KafkaSource,
    accepted: list[Accepted],
    *,
    source_kind: str,
    delivery: str | None = None,
) -> UpsertResult:
    """Land accepted records by (org_id, business key, version) in one statement (see module docstring)."""
    if not accepted:
        return UpsertResult([], 0, 0)
    # Postgres refuses to upsert the same key twice in one statement — keep the last of each.
    latest: dict[tuple[str, str, int], Accepted] = {}
    for record in accepted:
        latest[record.org_id, record.key, record.version] = record
    rows = list(latest.values())
    table = f'"{product.schema}"."{bronze_table(source.id)}"'
    contract_version = product.contracts[source.id].version
    result = conn.execute(
        text(
            f"""INSERT INTO {table} AS t (org_id, business_key, version, payload, payload_hash, event_ts,
                    source_kind, delivery, contract_version, filter_value, search_text)
                SELECT o, k, v, CAST(p AS jsonb), h, CAST(e AS timestamptz), :source_kind, :delivery,
                       :contract_version, f, to_tsvector('simple', st)
                FROM unnest(CAST(:o AS text[]), CAST(:k AS text[]), CAST(:v AS int[]), CAST(:p AS text[]),
                            CAST(:h AS text[]), CAST(:e AS text[]), CAST(:f AS text[]), CAST(:st AS text[]))
                     AS u(o, k, v, p, h, e, f, st)
                ON CONFLICT (org_id, business_key, version) DO UPDATE SET
                    payload = EXCLUDED.payload, payload_hash = EXCLUDED.payload_hash, event_ts = EXCLUDED.event_ts,
                    received_at = now(), source_kind = EXCLUDED.source_kind, delivery = EXCLUDED.delivery,
                    filter_value = EXCLUDED.filter_value, search_text = EXCLUDED.search_text,
                    corrections = t.corrections + 1, archived_at = NULL
                WHERE t.payload_hash <> EXCLUDED.payload_hash
                RETURNING org_id, business_key, version, (xmax = 0) AS inserted"""
        ),
        {
            "source_kind": source_kind,
            "delivery": delivery,
            "contract_version": contract_version,
            "o": [r.org_id for r in rows],
            "k": [r.key for r in rows],
            "v": [r.version for r in rows],
            "p": [json.dumps(r.payload, ensure_ascii=False) for r in rows],
            "h": [r.payload_hash for r in rows],
            "e": [r.event_ts for r in rows],
            "f": [r.filter_value for r in rows],
            "st": [r.search_text for r in rows],
        },
    ).all()
    inserted_keys = {(o, k, v) for o, k, v, ins in result if ins}
    corrected = sum(1 for *_, ins in result if not ins)
    return UpsertResult(
        inserted=[latest[key] for key in inserted_keys],
        corrected=corrected,
        duplicates=len(accepted) - len(result),
    )


def insert_quarantine(
    conn: Connection,
    product: DataProduct,
    source_id: str,
    rejected: list[Rejected],
    *,
    source_kind: str,
    delivery: str | None = None,
) -> None:
    """Keep rejected records (payload, or the raw text when it wasn't JSON) with their violations."""
    if not rejected:
        return
    conn.execute(
        text(
            f"""INSERT INTO "{product.schema}".ops_quarantine
                    (org_id, source_id, business_key, payload, raw, violations, source_kind, delivery, filter_value)
                VALUES (:org_id, :source_id, :key, CAST(:payload AS jsonb), :raw, CAST(:violations AS jsonb),
                        :source_kind, :delivery, :filter_value)"""
        ),
        [
            {
                "org_id": r.org_id,
                "source_id": source_id,
                "key": r.key,
                "payload": json.dumps(r.payload, ensure_ascii=False) if r.payload is not None else None,
                "raw": (r.raw or "")[:20_000] if r.payload is None else None,
                "violations": json.dumps(r.violations),
                "source_kind": source_kind,
                "delivery": delivery,
                "filter_value": r.filter_value,
            }
            for r in rejected
        ],
    )


HotKey = tuple[str, str, str, tuple[str, ...]]  # (aggregate, org_id, minute ISO, dimension values)


def hot_increments(product: DataProduct, source: KafkaSource, new_records: list[Accepted]) -> dict[HotKey, list[int]]:
    """Per-minute measure increments for the *first* sighting of each key."""
    increments: dict[HotKey, list[int]] = defaultdict(lambda: [])
    for agg in product.config.hot_path:
        if source.id not in agg.sources:
            continue
        for record in new_records:
            if source.version_field and record.version != 1:
                continue
            stamp = parse_timestamp(record.event_ts) or datetime.now(UTC)
            minute = stamp.astimezone(UTC).replace(second=0, microsecond=0).isoformat()
            dims = tuple(str(get_one(record.payload, path) or "") for path in agg.dimensions.values())
            key = (agg.name, record.org_id, minute, dims)
            values = increments[key] or [0] * len(agg.measures)
            for i, measure in enumerate(agg.measures):
                if measure.agg == "count":
                    values[i] += 1
                else:
                    values[i] += int(
                        sum(v for v in get_path(record.payload, measure.path or "") if isinstance(v, int | float))
                    )
            increments[key] = values
    return dict(increments)


def apply_hot(conn: Connection, product: DataProduct, increments: dict[HotKey, list[int]]) -> None:
    """Add the increments to each hot aggregate's per-minute rows (one upsert per aggregate)."""
    by_agg: dict[str, list[tuple[HotKey, list[int]]]] = defaultdict(list)
    for key, values in increments.items():
        by_agg[key[0]].append((key, values))
    for agg in product.config.hot_path:
        items = by_agg.get(agg.name)
        if not items:
            continue
        dims = list(agg.dimensions)
        measures = [m.name for m in agg.measures]
        cols = ", ".join(["org_id", "minute", *(f'"{d}"' for d in dims), *(f'"{m}"' for m in measures)])
        params = ", ".join([
            ":org_id",
            "CAST(:minute AS timestamptz)",
            *(f":d_{i}" for i in range(len(dims))),
            *(f":m_{i}" for i in range(len(measures))),
        ])
        conflict = ", ".join(["org_id", "minute", *(f'"{d}"' for d in dims)])
        updates = ", ".join([*(f'"{m}" = h."{m}" + EXCLUDED."{m}"' for m in measures), "updated_at = now()"])
        conn.execute(
            text(
                f'INSERT INTO "{product.schema}"."{hot_table(agg.name)}" AS h ({cols}) VALUES ({params}) '
                f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
            ),
            [
                {
                    "org_id": key[1],
                    "minute": key[2],
                    **{f"d_{i}": v for i, v in enumerate(key[3])},
                    **{f"m_{i}": v for i, v in enumerate(values)},
                }
                for key, values in items
            ],
        )


class ReferenceCache:
    """known_value rule lookups against the CDC-captured current state (`cdc_<table>`),
    refreshed every `ttl` seconds per (org, table, column). An empty reference set means "not
    captured yet" and accepts everything — never quarantine on our own ignorance.
    """

    def __init__(self, engine: Engine, product: DataProduct, ttl: float = 30.0) -> None:
        """Bind the cache to one data product's CDC current-state tables."""
        self._engine, self._product, self._ttl = engine, product, ttl
        self._sets: dict[tuple[str, str, str], tuple[float, set[str]]] = {}

    def for_org(self, org_id: str) -> Callable[[str, str, Any], bool]:
        """A known_value lookup bound to one tenant."""

        def lookup(table: str, column: str, value: Any) -> bool:
            known = self._values(org_id, table, column)
            return not known or str(value) in known

        return lookup

    def _values(self, org_id: str, table: str, column: str) -> set[str]:
        key = (org_id, table, column)
        cached = self._sets.get(key)
        if cached is not None and time.monotonic() - cached[0] < self._ttl:
            return cached[1]
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f'SELECT DISTINCT row ->> :column FROM "{self._product.schema}"."cdc_{table}" '
                    "WHERE org_id = :org_id AND op <> 'DELETE'"
                ),
                {"column": column, "org_id": org_id},
            ).all()
        values = {r[0] for r in rows if r[0] is not None}
        if len(self._sets) > 1000:
            self._sets.clear()
        self._sets[key] = (time.monotonic(), values)
        return values
