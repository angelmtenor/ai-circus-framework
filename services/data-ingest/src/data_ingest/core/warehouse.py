"""
- Title:    Warehouse bootstrap — databases and the per-data-product bronze/hot/ops tables
- Author:   Angel Martinez-Tenor

data-ingest owns everything below silver (docs/adr/0004): for each data product, schema
`<slug>` in the warehouse database holds

- `bronze_<source>` — every valid record as received, by (org_id, business key, version),
  with the payload hash that makes re-sends a no-op and corrections countable;
- `hot_<aggregate>` — per-minute live aggregates (ADR 0012);
- `bronze_cdc_<table>` (change log) and `cdc_<table>` (current state) per captured table;
- `ops_quarantine` and `ops_file_deliveries` — what failed, and what each file delivered vs
  its control file.

dbt (data-transform) builds silver/gold from these. Every table carries `org_id`.
"""

from __future__ import annotations

from ai_circus_shared.data_product import bronze_table, hot_table
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

from data_ingest.core.products import DataProduct


def ensure_databases(url: URL, names: list[str]) -> None:
    """CREATE DATABASE for each missing name (needs CREATEDB; idempotent). Connects to the
    cluster's maintenance database, since a database can't be created from inside itself.
    """
    engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            existing = {row[0] for row in conn.execute(text("SELECT datname FROM pg_database"))}
            for name in names:
                if name not in existing:
                    conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()


def _q(identifier: str) -> str:
    return f'"{identifier}"'


def bootstrap_product(engine: Engine, product: DataProduct) -> None:
    """Create the data product's schema and tables (idempotent, additive)."""
    s = _q(product.schema)
    statements = [f"CREATE SCHEMA IF NOT EXISTS {s}"]
    for source in product.config.kafka_sources():
        t = f"{s}.{_q(bronze_table(source.id))}"
        statements += [
            f"""CREATE TABLE IF NOT EXISTS {t} (
                org_id text NOT NULL, business_key text NOT NULL, version int NOT NULL DEFAULT 1,
                payload jsonb NOT NULL, payload_hash text NOT NULL, event_ts timestamptz,
                received_at timestamptz NOT NULL DEFAULT now(), source_kind text NOT NULL, delivery text,
                contract_version text, filter_value text, search_text tsvector,
                corrections int NOT NULL DEFAULT 0, archived_at timestamptz,
                PRIMARY KEY (org_id, business_key, version))""",
            f"CREATE INDEX IF NOT EXISTS {_q(f'ix_{source.id}_received')} ON {t} (org_id, received_at)",
            f"CREATE INDEX IF NOT EXISTS {_q(f'ix_{source.id}_event')} ON {t} (org_id, event_ts)",
            f"CREATE INDEX IF NOT EXISTS {_q(f'ix_{source.id}_search')} ON {t} USING gin (search_text)",
            f"CREATE INDEX IF NOT EXISTS {_q(f'ix_{source.id}_unarchived')} ON {t} (received_at) "
            "WHERE archived_at IS NULL",
        ]
    for agg in product.config.hot_path:
        dims = ", ".join(f"{_q(d)} text NOT NULL" for d in agg.dimensions)
        measures = ", ".join(f"{_q(m.name)} bigint NOT NULL DEFAULT 0" for m in agg.measures)
        keys = ", ".join(["org_id", "minute", *(_q(d) for d in agg.dimensions)])
        statements.append(
            f"""CREATE TABLE IF NOT EXISTS {s}.{_q(hot_table(agg.name))} (org_id text NOT NULL,
                minute timestamptz NOT NULL, {dims}, {measures}, updated_at timestamptz DEFAULT now(),
                PRIMARY KEY ({keys}))"""
        )
    for source in product.config.cdc_sources():
        for table in source.tables:
            statements += [
                f"""CREATE TABLE IF NOT EXISTS {s}.{_q(f"bronze_cdc_{table.name}")} (
                    change_id bigserial PRIMARY KEY, org_id text NOT NULL, key text NOT NULL, op text NOT NULL,
                    row jsonb, lsn text, captured_at timestamptz NOT NULL DEFAULT now())""",
                f"""CREATE TABLE IF NOT EXISTS {s}.{_q(f"cdc_{table.name}")} (org_id text NOT NULL,
                    key text NOT NULL, row jsonb NOT NULL, op text NOT NULL, changed_at timestamptz NOT NULL,
                    PRIMARY KEY (org_id, key))""",
            ]
    statements += [
        f"""CREATE TABLE IF NOT EXISTS {s}.ops_quarantine (quarantine_id bigserial PRIMARY KEY,
            org_id text NOT NULL, source_id text NOT NULL, business_key text, payload jsonb, raw text,
            violations jsonb NOT NULL, source_kind text NOT NULL, delivery text, filter_value text,
            received_at timestamptz NOT NULL DEFAULT now(), day date NOT NULL DEFAULT current_date,
            status text NOT NULL DEFAULT 'open')""",
        f"CREATE INDEX IF NOT EXISTS ix_quarantine_org ON {s}.ops_quarantine (org_id, received_at)",
        f"""CREATE TABLE IF NOT EXISTS {s}.ops_file_deliveries (org_id text NOT NULL, source_id text NOT NULL,
            delivery text NOT NULL, business_date date, partition jsonb, expected_count int, loaded_count int,
            quarantined_count int, expected_sum bigint, loaded_sum bigint, status text NOT NULL,
            loaded_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (org_id, source_id, delivery))""",
    ]
    with engine.begin() as conn:
        # One data-ingest replica normally — the lock keeps a rolling restart's two pods
        # from racing the same CREATE statements.
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"bootstrap:{product.slug}"})
        for statement in statements:
            conn.execute(text(statement))
