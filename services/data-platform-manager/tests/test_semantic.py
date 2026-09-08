"""Tests for core/semantic.py.

`run_query()` itself needs real Postgres (platform-registry's `entitlements`/
`scenarios` tables) and a real Iceberg catalog, so it isn't unit-testable here
(same reasoning as core/lakehouse.py, which has no dedicated test file either
— see test_api.py's mocked lakehouse.* tests). What *is* unit-testable without
any infra is the semantic model's actual SQL: these tests register small,
hand-built Arrow tables matching the real schemas `run_query()` would produce
and run each view's real SQL through a real, in-memory DuckDB connection —
proving the join/aggregation logic itself is correct, not just that it's
wired up.
"""

from __future__ import annotations

import duckdb
import pyarrow as pa
import pytest

from data_platform_manager.core.semantic import SEMANTIC_VIEWS, get_view


def test_get_view_returns_a_known_view_by_name() -> None:
    view = get_view("tenant_activity_360")

    assert view is not None
    assert view.name == "tenant_activity_360"
    assert view.sql.strip()


def test_get_view_returns_none_for_an_unknown_name() -> None:
    assert get_view("does-not-exist") is None


def test_semantic_views_have_unique_non_empty_names() -> None:
    names = [view.name for view in SEMANTIC_VIEWS]

    assert len(names) == len(set(names))
    assert all(names)


@pytest.fixture
def duckdb_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


def _lakehouse_table(rows: list[tuple[str, str, str]]) -> pa.Table:
    """rows: (org_id, doc_id, content_json) — mirrors lakehouse.ingest()'s real schema."""
    return pa.table({
        "org_id": [r[0] for r in rows],
        "doc_id": [r[1] for r in rows],
        "content": [r[2] for r in rows],
    })


def test_pipeline_trigger_counts_by_job_aggregates_real_content(duckdb_conn: duckdb.DuckDBPyConnection) -> None:
    duckdb_conn.register(
        "lakehouse_pipeline_triggers",
        _lakehouse_table([
            ("admin", "1", '{"job": "etl-tabular", "triggered_at": "2026-01-01T00:00:00+00:00"}'),
            ("admin", "2", '{"job": "etl-tabular", "triggered_at": "2026-01-02T00:00:00+00:00"}'),
            ("admin", "3", '{"job": "training", "triggered_at": "2026-01-03T00:00:00+00:00"}'),
        ]),
    )

    view = get_view("pipeline_trigger_counts_by_job")
    assert view is not None
    rows = duckdb_conn.execute(view.sql).to_arrow_table().to_pylist()

    by_job = {row["job"]: row for row in rows}
    assert by_job["etl-tabular"]["trigger_count"] == 2
    assert by_job["etl-tabular"]["last_triggered_at"] == "2026-01-02T00:00:00+00:00"
    assert by_job["training"]["trigger_count"] == 1


def test_scenario_entitlement_counts_joins_entitlements_to_scenarios(
    duckdb_conn: duckdb.DuckDBPyConnection,
) -> None:
    duckdb_conn.register(
        "pr_entitlements",
        pa.table({"org_id": ["admin", "admin", "engineering-demo"], "scenario_slug": ["churn", "mpm", "churn"]}),
    )
    duckdb_conn.register(
        "pr_scenarios",
        pa.table({
            "slug": ["churn", "mpm"],
            "kind": ["tabular_ml", "tabular_ml"],
            "industry": ["telecom", "manufacturing_industry"],
        }),
    )

    view = get_view("scenario_entitlement_counts")
    assert view is not None
    rows = duckdb_conn.execute(view.sql).to_arrow_table().to_pylist()

    by_industry = {row["industry"]: row["entitlement_count"] for row in rows}
    assert by_industry["telecom"] == 2
    assert by_industry["manufacturing_industry"] == 1


def test_tenant_activity_360_federates_lakehouse_and_entitlements(duckdb_conn: duckdb.DuckDBPyConnection) -> None:
    # "admin" has both pipeline activity and entitlements; "orphan-tenant" has
    # only lakehouse activity; "unused-tenant" has only entitlements. The FULL
    # OUTER JOIN must surface all three, not just the intersection.
    duckdb_conn.register(
        "lakehouse_pipeline_triggers",
        _lakehouse_table([
            ("admin", "1", '{"job": "etl-tabular", "triggered_at": "2026-01-01T00:00:00+00:00"}'),
            ("orphan-tenant", "2", '{"job": "training", "triggered_at": "2026-01-02T00:00:00+00:00"}'),
        ]),
    )
    duckdb_conn.register(
        "pr_entitlements",
        pa.table({"org_id": ["admin", "unused-tenant"], "scenario_slug": ["churn", "mpm"]}),
    )

    view = get_view("tenant_activity_360")
    assert view is not None
    rows = duckdb_conn.execute(view.sql).to_arrow_table().to_pylist()

    by_org = {row["org_id"]: row for row in rows}
    assert by_org["admin"]["pipeline_triggers_captured"] == 1
    assert by_org["admin"]["scenarios_entitled"] == 1
    assert by_org["orphan-tenant"]["pipeline_triggers_captured"] == 1
    assert by_org["orphan-tenant"]["scenarios_entitled"] == 0
    assert by_org["unused-tenant"]["pipeline_triggers_captured"] == 0
    assert by_org["unused-tenant"]["scenarios_entitled"] == 1
