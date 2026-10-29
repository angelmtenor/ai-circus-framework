"""
- Title:    Warehouse access — Postgres always, ClickHouse for the marts when the analytics tier runs
- Author:   Angel Martinez-Tenor

Bronze, hot and ops tables are written by data-ingest into Postgres and are always read from
there. Silver and gold are built by dbt into Postgres (core tier) or ClickHouse (analytics tier,
docs/adr/0005); `MARTS_ENGINE` picks which one this instance reads. Queries are built by the
callers from whitelisted identifiers only; values always travel as bound parameters, written
with `Query.param` so the same builder works with both engines' placeholder syntax.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ai_circus_shared.data_product import TRANSFORM_RUNS_TABLE, bronze_table, layer_schema
from sqlalchemy import Engine, text

from data_api.core.products import DataProduct

_CH_TYPES = {str: "String", int: "Int64", float: "Float64", date: "Date", datetime: "DateTime64(3, 'UTC')"}


@dataclass
class Query:
    """A SQL statement under construction with bound parameters for one dialect."""

    dialect: str  # "postgres" | "clickhouse"
    params: dict[str, Any] = field(default_factory=dict)

    def param(self, value: Any, *, array: bool = False) -> str:
        """Bind a value and return its placeholder in this dialect."""
        name = f"p{len(self.params)}"
        self.params[name] = list(value) if array else value
        if self.dialect == "postgres":
            return f"CAST(:{name} AS text[])" if array else f":{name}"
        if array:
            return f"{{{name}:Array(String)}}"
        ch_type = "Nullable(String)" if value is None else _CH_TYPES.get(type(value), "String")
        return f"{{{name}:{ch_type}}}"

    def ident(self, *parts: str) -> str:
        """A quoted (schema-qualified) identifier in this dialect."""
        quote = '"' if self.dialect == "postgres" else "`"
        return ".".join(f"{quote}{p}{quote}" for p in parts)

    def in_list(self, column: str, values: tuple[str, ...] | list[str]) -> str:
        """`column IN values` with the values bound as one array."""
        if self.dialect == "postgres":
            return f"{column}::text = ANY({self.param(values, array=True)})"
        return f"toString({column}) IN {self.param(values, array=True)}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


class ClickHouseReader:
    """Reads the marts from ClickHouse (analytics tier)."""

    def __init__(self, url: str, user: str | None, password: str | None) -> None:
        """Connect over HTTP."""
        import clickhouse_connect  # imported lazily: only the analytics tier needs it

        # No session: requests share this client from FastAPI's threadpool, and ClickHouse
        # refuses concurrent queries within one session.
        self._client = clickhouse_connect.get_client(
            dsn=url, username=user or "default", password=password or "", autogenerate_session_id=False
        )

    def rows(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a query and return JSON-safe dict rows."""
        result = self._client.query(sql, parameters=params)
        return [{k: _jsonable(v) for k, v in zip(result.column_names, row, strict=True)} for row in result.result_rows]


class Warehouse:
    """One door to every table a data product serves, whichever engine holds it."""

    def __init__(self, engine: Engine, marts_engine: str, clickhouse: ClickHouseReader | None = None) -> None:
        """Postgres always; ClickHouse for silver/gold when configured."""
        self.engine = engine
        self.marts_engine = marts_engine if clickhouse is not None else "postgres"
        self._clickhouse = clickhouse
        self._columns: dict[tuple[str, str, str], tuple[float, list[str]]] = {}

    def dialect_for(self, layer: str) -> str:
        """Which engine holds a layer."""
        return self.marts_engine if layer in ("silver", "gold") else "postgres"

    def query(self, layer: str) -> Query:
        """A new Query in the dialect of a layer."""
        return Query(self.dialect_for(layer))

    def rows(self, query: Query, sql: str, *, timeout_ms: int = 15_000) -> list[dict[str, Any]]:
        """Run a read-only query (Postgres: in a read-only transaction with a statement timeout)."""
        if query.dialect == "clickhouse":
            assert self._clickhouse is not None
            return self._clickhouse.rows(sql, query.params)
        with self.engine.begin() as conn:
            conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            conn.execute(text("SET TRANSACTION READ ONLY"))
            result = conn.execute(text(sql), query.params)
            return [{str(k): _jsonable(v) for k, v in row.items()} for row in result.mappings()]

    def relation(self, product: DataProduct, layer: str, name: str) -> tuple[str, str]:
        """(schema, table) of a dataset in its layer."""
        return layer_schema(product.slug, layer), name

    def columns(self, product: DataProduct, layer: str, name: str) -> list[str]:
        """The dataset's columns ([] when dbt hasn't built it yet); cached a minute."""
        schema, table = self.relation(product, layer, name)
        key = (self.dialect_for(layer), schema, table)
        cached = self._columns.get(key)
        if cached is not None and time.monotonic() - cached[0] < 60:
            return cached[1]
        if key[0] == "clickhouse":
            assert self._clickhouse is not None
            found = [
                r["name"]
                for r in self._clickhouse.rows(
                    "SELECT name FROM system.columns WHERE database = {db:String} AND table = {t:String} "
                    "ORDER BY position",
                    {"db": schema, "t": table},
                )
            ]
        else:
            with self.engine.connect() as conn:
                found = list(
                    conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"
                        ),
                        {"s": schema, "t": table},
                    ).scalars()
                )
        self._columns[key] = (time.monotonic(), found)
        return found

    def org_has_data(self, product: DataProduct, org_id: str) -> bool:
        """Whether a tenant has any record of this data product yet."""
        sources = product.config.kafka_sources()
        if not sources:
            return False
        with self.engine.connect() as conn:
            return bool(
                conn.execute(
                    text(
                        f'SELECT EXISTS (SELECT 1 FROM "{product.slug}"."{bronze_table(sources[0].id)}" '
                        "WHERE org_id = :o)"
                    ),
                    {"o": org_id},
                ).scalar()
            )

    def last_transform(self, product: DataProduct) -> dict[str, Any] | None:
        """The latest data-transform run (any outcome) plus `curated_through`: the watermark of
        the latest *successful* one — everything received up to it is in gold.
        """
        runs = f'"{product.slug}"."{TRANSFORM_RUNS_TABLE}"'
        with self.engine.connect() as conn:
            if conn.execute(text("SELECT to_regclass(:t)"), {"t": runs}).scalar() is None:
                return None
            rows = (
                conn
                .execute(
                    text(
                        f"""SELECT run_id, target, status, started_at, finished_at, watermark, engine, models_ok,
                           tests_passed, tests_failed, message
                    FROM {runs} ORDER BY started_at DESC LIMIT 1"""
                    )
                )
                .mappings()
                .all()
            )
            curated = conn.execute(text(f"SELECT max(watermark) FROM {runs} WHERE status = 'success'")).scalar()
        if not rows:
            return None
        latest = {str(k): _jsonable(v) for k, v in rows[0].items()}
        latest["curated_through"] = curated.isoformat() if curated else None
        return latest

    def transform_runs(self, product: DataProduct, limit: int) -> list[dict[str, Any]]:
        """The latest data-transform runs, newest first."""
        runs = f'"{product.slug}"."{TRANSFORM_RUNS_TABLE}"'
        with self.engine.connect() as conn:
            if conn.execute(text("SELECT to_regclass(:t)"), {"t": runs}).scalar() is None:
                return []
            rows = (
                conn
                .execute(
                    text(
                        f"""SELECT run_id, target, status, started_at, finished_at, watermark, models_ok, models_failed,
                           tests_passed, tests_failed, message, failures
                    FROM {runs} ORDER BY started_at DESC LIMIT :n"""
                    ),
                    {"n": limit},
                )
                .mappings()
                .all()
            )
        return [{str(k): _jsonable(v) for k, v in row.items()} for row in rows]
