"""
- Title:    Semantic modeling & query federation (DuckDB)
- Author:   ai-circus-framework contributors

A small, named catalog of business-friendly queries (the "semantic model" —
see SEMANTIC_VIEWS below) run through an embedded DuckDB engine that federates
two genuinely separate data sources in one SQL statement, without copying
either into a new store:

- The lakehouse's own `pipeline_triggers` Iceberg table (see core/lakehouse.py)
  — handed to DuckDB directly as an in-memory Arrow table via
  `conn.register()`, no extension needed.
- `platform-registry`'s real `entitlements`/`scenarios` tables — a genuinely
  different service's database (`platform`, not this service's own
  `data_platform_manager`), reached over a short-lived, read-only psycopg
  connection using the same shared cluster credentials every service already
  has (see docker-compose.yml's POSTGRES_USER/PASSWORD), then likewise handed
  to DuckDB as Arrow tables.

DuckDB does ship a `postgres` extension that can attach a live Postgres
database and push down/join without copying at all — deliberately not used
here, since loading it downloads a platform-specific binary from
extensions.duckdb.org at first use, a runtime network dependency this laptop
demo shouldn't need. Fetching the (small, admin-only, already-real) rows
ourselves and registering them as Arrow tables gets the same "one SQL query
across heterogeneous sources" result without that dependency — still a real
federated join, just materialized on this side of the wire instead of pushed
down to Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import duckdb
import psycopg
import pyarrow as pa

from data_platform_manager.core import lakehouse

_PLATFORM_REGISTRY_DB = "platform"


@dataclass(frozen=True)
class SemanticView:
    """One named entry in the semantic model — a business-friendly question
    backed by a real SQL query against the views registered in run_query().
    """

    name: str
    description: str
    sql: str


SEMANTIC_VIEWS: list[SemanticView] = [
    SemanticView(
        name="pipeline_trigger_counts_by_job",
        description="How many times each pipeline job has been triggered, and when it last ran — "
        "lakehouse-only (Iceberg via PyIceberg -> Arrow -> DuckDB).",
        sql="""
            SELECT
                json_extract_string(content, '$.job') AS job,
                count(*) AS trigger_count,
                max(json_extract_string(content, '$.triggered_at')) AS last_triggered_at
            FROM lakehouse_pipeline_triggers
            GROUP BY 1
            ORDER BY 1
        """,
    ),
    SemanticView(
        name="scenario_entitlement_counts",
        description="How many tenant entitlements exist per scenario kind/industry — "
        "platform-registry-only (a real, different service's Postgres database).",
        sql="""
            SELECT
                s.industry,
                s.kind,
                count(*) AS entitlement_count
            FROM pr_entitlements e
            JOIN pr_scenarios s ON s.slug = e.scenario_slug
            GROUP BY 1, 2
            ORDER BY 1, 2
        """,
    ),
    SemanticView(
        name="tenant_activity_360",
        description="Per-tenant pipeline activity (lakehouse) next to how many scenarios that "
        "tenant is entitled to (platform-registry) — the genuinely federated one: one tenant, "
        "two unrelated data sources, joined by org_id in a single query.",
        sql="""
            SELECT
                coalesce(t.org_id, e.org_id) AS org_id,
                count(DISTINCT t.doc_id) AS pipeline_triggers_captured,
                count(DISTINCT e.scenario_slug) AS scenarios_entitled
            FROM lakehouse_pipeline_triggers t
            FULL OUTER JOIN pr_entitlements e ON e.org_id = t.org_id
            GROUP BY 1
            ORDER BY 1
        """,
    ),
]

_VIEWS_BY_NAME = {view.name: view for view in SEMANTIC_VIEWS}


class SemanticConfig(lakehouse.LakehouseConfig, Protocol):
    """Shape a service's own EnvConfig must satisfy to call run_query() — same
    fields as the lakehouse, since both connect to Postgres and both read the
    same lakehouse catalog.
    """


def get_view(name: str) -> SemanticView | None:
    """One semantic view definition by name, or None if unknown."""
    return _VIEWS_BY_NAME.get(name)


def _empty_entitlements() -> pa.Table:
    return pa.table({
        "org_id": pa.array([], type=pa.string()),
        "scenario_slug": pa.array([], type=pa.string()),
    })


def _empty_scenarios() -> pa.Table:
    return pa.table({
        "slug": pa.array([], type=pa.string()),
        "kind": pa.array([], type=pa.string()),
        "industry": pa.array([], type=pa.string()),
    })


def _load_platform_registry_tables(config: SemanticConfig) -> tuple[pa.Table, pa.Table]:
    """Real rows from platform-registry's own `entitlements`/`scenarios` tables —
    a genuinely different service's database on the same shared Postgres cluster.
    Falls back to empty (correctly-typed) tables if that database/schema isn't
    there yet (platform-registry never started), same "no data yet, not an
    error" leniency as every other optional-dependency panel in this service.
    """
    password = config.POSTGRES_PASSWORD.get_secret_value()
    try:
        with (
            psycopg.connect(
                host=config.POSTGRES_HOST,
                port=config.POSTGRES_PORT,
                dbname=_PLATFORM_REGISTRY_DB,
                user=config.POSTGRES_USER,
                password=password,
            ) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SELECT org_id, scenario_slug FROM entitlements")
            entitlement_rows = cur.fetchall()
            cur.execute("SELECT slug, kind, industry FROM scenarios")
            scenario_rows = cur.fetchall()
    except psycopg.Error:
        return _empty_entitlements(), _empty_scenarios()

    entitlements = (
        pa.table({
            "org_id": [row[0] for row in entitlement_rows],
            "scenario_slug": [row[1] for row in entitlement_rows],
        })
        if entitlement_rows
        else _empty_entitlements()
    )
    scenarios = (
        pa.table({
            "slug": [row[0] for row in scenario_rows],
            "kind": [row[1] for row in scenario_rows],
            "industry": [row[2] for row in scenario_rows],
        })
        if scenario_rows
        else _empty_scenarios()
    )
    return entitlements, scenarios


def run_query(view: SemanticView, config: SemanticConfig, lakehouse_table_name: str) -> dict[str, Any]:
    """Execute one semantic view: register both real data sources as in-memory
    DuckDB tables, then run the view's federated SQL against them. A fresh,
    short-lived DuckDB connection per call — cheap, no persistent state.
    `lakehouse_table_name` is the caller's `_LAKEHOUSE_TABLE_NAME` (api.py) —
    passed in rather than hardcoded here to keep this module independent of
    that constant.
    """
    catalog = lakehouse.get_catalog(config)
    pipeline_triggers = lakehouse.load_arrow(catalog, lakehouse_table_name)
    entitlements, scenarios = _load_platform_registry_tables(config)

    conn = duckdb.connect(":memory:")
    try:
        conn.register("lakehouse_pipeline_triggers", pipeline_triggers)
        conn.register("pr_entitlements", entitlements)
        conn.register("pr_scenarios", scenarios)
        result = conn.execute(view.sql).to_arrow_table()
    finally:
        conn.close()

    return {"view": view.name, "columns": result.column_names, "rows": result.to_pylist()}
