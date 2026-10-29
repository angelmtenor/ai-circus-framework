"""The Dagster code location: built from a real `dbt parse` of the retail project."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from data_transform.core.products import load_products
from data_transform.core.runner import write_profiles
from data_transform.dagster_defs import build_definitions
from tests.fakes import FakeEngine

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("DBT_POSTGRES_PASSWORD", "unused-by-parse")
    monkeypatch.setenv("DBT_CLICKHOUSE_PASSWORD", "")
    monkeypatch.setenv("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
    profiles = write_profiles(
        tmp_path / "profiles",
        host="localhost",
        port="5432",
        user="u",
        database="d",
        threads=1,
        clickhouse_host="localhost",
        clickhouse_user="u",
    ).parent
    return SimpleNamespace(
        products=load_products(SCENARIOS, "surf_retail"),
        profiles=profiles,
        artifacts=tmp_path / "artifacts",
        engine=FakeEngine(),
        targets=["postgres", "clickhouse"],
    )


def test_definitions_have_assets_jobs_sensors_and_schedule(env: SimpleNamespace) -> None:
    defs = build_definitions(env, after_run=lambda *_a: None)
    keys = {key.to_user_string() for key in defs.resolve_asset_graph().get_all_asset_keys()}
    assert "surf_retail/postgres/mart_sales_daily" in keys
    assert "surf_retail/clickhouse/mart_sales_daily" in keys
    assert "surf_retail/postgres/stg_receipts" in keys
    assert "surf_retail/clickhouse/stg_receipts" not in keys  # disabled on ClickHouse
    jobs = {job.name for job in defs.resolve_all_job_defs()}
    assert {"surf_retail_postgres_build", "surf_retail_clickhouse_build"} <= jobs
    sensors = {s.name for s in defs.sensors or []}
    assert sensors == {"surf_retail_new_bronze", "surf_retail_clickhouse_after_postgres"}
    assert [s.name for s in defs.schedules or []] == ["surf_retail_nightly"]
    assert not list((env.artifacts / "surf_retail" / "postgres").glob("parse-*"))  # cleaned up
