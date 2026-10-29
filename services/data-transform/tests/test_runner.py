"""dbt runner: profiles, run summaries, watermark, locking, pseudonymisation, publishing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from data_transform.core import clickhouse, runner
from data_transform.core.products import DataProduct, load_products
from data_transform.core.publish import publish_events, upload_artifacts
from data_transform.core.runner import RunResult, _summarise, install_pseudonymisation, transform, write_profiles
from tests.fakes import FakeEngine

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


@pytest.fixture(scope="module")
def product() -> DataProduct:
    return load_products(SCENARIOS, "surf_retail")["surf_retail"]


def test_profiles_never_hold_a_password(tmp_path: Path) -> None:
    path = write_profiles(
        tmp_path,
        host="postgres",
        port="5432",
        user="u",
        database="data_platform",
        threads=2,
        clickhouse_host="clickhouse",
        clickhouse_user="ch",
    )
    profile = yaml.safe_load(path.read_text())["data_platform"]
    expected = "{{ env_var('DBT_POSTGRES_PASSWORD') }}"  # a dbt template, not a secret
    assert profile["outputs"]["postgres"]["password"] == expected
    assert profile["outputs"]["clickhouse"]["user"] == "ch"
    no_ch = yaml.safe_load(
        write_profiles(
            tmp_path,
            host="h",
            port="5432",
            user="u",
            database="d",
            threads=1,
            clickhouse_host=None,
            clickhouse_user=None,
        ).read_text()
    )
    assert set(no_ch["data_platform"]["outputs"]) == {"postgres"}


def test_summarise_counts_models_tests_and_failures() -> None:
    result = RunResult(slug="s", target="postgres")
    _summarise(
        result,
        {
            "results": [
                {"unique_id": "model.p.a", "status": "success"},
                {"unique_id": "model.p.b", "status": "error", "message": "boom"},
                {"unique_id": "test.p.t1", "status": "pass"},
                {"unique_id": "test.p.t2", "status": "fail", "message": "3 rows"},
                {"unique_id": "test.p.t3", "status": "warn"},
            ]
        },
    )
    assert (result.models_ok, result.models_failed, result.tests_passed, result.tests_failed) == (1, 1, 1, 1)
    assert [f["node"] for f in result.failures] == ["model.p.b", "test.p.t2", "test.p.t3"]


def test_pseudonymisation_key_is_bound_never_in_sql() -> None:
    engine = FakeEngine()
    install_pseudonymisation(engine, "s3cret-key")  # type: ignore[arg-type]
    assert all("s3cret-key" not in sql for sql, _ in engine.statements)
    assert any(params == {"k": "s3cret-key"} for _, params in engine.statements)
    assert any("FUNCTION data_platform_private.pseudonymise" in sql for sql, _ in engine.statements)


def test_watermark_unions_every_bronze_table(product: DataProduct) -> None:
    stamp = datetime(2026, 10, 28, 12, tzinfo=UTC)
    engine = FakeEngine(lambda sql, _p: [(stamp,)] if "least(" in sql else None)
    assert runner.bronze_watermark(engine, product) == stamp  # type: ignore[arg-type]
    sql = engine.statements[0][0]
    assert "bronze_pos_receipts" in sql and "bronze_web_orders" in sql and "interval '5 seconds'" in sql


def test_transform_skips_when_another_build_holds_the_lock(product: DataProduct, tmp_path: Path) -> None:
    engine = FakeEngine(lambda sql, _p: [(False,)] if "pg_try_advisory_lock" in sql else None)
    result = transform(engine, product, "postgres", tmp_path, tmp_path)  # type: ignore[arg-type]
    assert result.status == "skipped"


def test_transform_records_success(product: DataProduct, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run_dbt(_product: Any, _target: str, _profiles: Path, _artifacts: Path, command: list[str]) -> Any:
        calls.append(command)
        return True, {
            "results": [{"unique_id": "model.p.a", "status": "success"}, {"unique_id": "test.p.t", "status": "pass"}]
        }

    monkeypatch.setattr(runner, "run_dbt", fake_run_dbt)
    engine = FakeEngine(lambda sql, _p: [(True,)] if "pg_try_advisory_lock" in sql else [(None,)])
    result = transform(engine, product, "postgres", tmp_path, tmp_path)  # type: ignore[arg-type]
    assert result.status == "success" and calls == [["build"], ["docs", "generate"]]
    recorded = [p for s, p in engine.statements if "ops_transform_runs" in s and "INSERT" in s]
    assert recorded[-1]["status"] == "success" and recorded[-1]["models_ok"] == 1
    assert any("pg_advisory_unlock" in s for s, _ in engine.statements)


def test_transform_failure_is_recorded(product: DataProduct, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any) -> Any:
        raise RuntimeError("dbt exploded")

    monkeypatch.setattr(runner, "run_dbt", boom)
    engine = FakeEngine(lambda sql, _p: [(True,)] if "pg_try_advisory_lock" in sql else [(None,)])
    result = transform(engine, product, "postgres", tmp_path, tmp_path)  # type: ignore[arg-type]
    assert result.status == "failed" and "exploded" in result.message


class FakeClickHouse:
    """Answers EXISTS DATABASE and records commands."""

    def __init__(self, exists: bool) -> None:
        """Script whether the database already exists."""
        self.exists, self.commands = exists, []

    def command(self, sql: str) -> str:
        """Record one command."""
        self.commands.append(sql)
        return "1" if sql.startswith("EXISTS") and self.exists else "0"


def test_clickhouse_prepare_creates_the_postgres_engine_database_once(product: DataProduct) -> None:
    client = FakeClickHouse(exists=False)
    clickhouse.prepare(
        client,
        product,
        pg_host="postgres",
        pg_port="5432",
        pg_database="data_platform",
        pg_user="u",
        pg_password="it's",  # ruff: ignore[hardcoded-password-func-arg]
    )
    creates = [c for c in client.commands if c.startswith("CREATE")]
    assert "ENGINE = PostgreSQL('postgres:5432', 'data_platform', 'u', 'it\\'s', 'surf_retail_silver')" in creates[0]
    assert "`surf_retail_pg_gold`" in creates[1] and creates[1].endswith("'surf_retail_gold')")
    already = FakeClickHouse(exists=True)
    clickhouse.prepare(already, product, pg_host="p", pg_port="1", pg_database="d", pg_user="u", pg_password="p")  # ruff: ignore[hardcoded-password-func-arg]
    assert len(already.commands) == 2  # one EXISTS per database, nothing created


class FakeProducer:
    """Collects produced messages."""

    def __init__(self) -> None:
        """Start empty."""
        self.messages: list[tuple[str, bytes]] = []

    def produce(self, topic: str, value: bytes) -> None:
        """Collect one message."""
        self.messages.append((topic, value))

    def flush(self, _timeout: float) -> int:
        """Nothing pending."""
        return 0


class FakeStore:
    """Collects uploads."""

    def __init__(self) -> None:
        """Start empty."""
        self.objects: dict[str, bytes] = {}

    def put(self, org: str, key: str, data: bytes) -> str:
        """Store one object."""
        self.objects[f"{org}/{key}"] = data
        return key


def test_publish_events_per_tenant_and_artifacts(tmp_path: Path) -> None:
    result = RunResult(
        slug="surf_retail", target="postgres", status="success", watermark=datetime(2026, 10, 28, tzinfo=UTC)
    )
    producer = FakeProducer()
    publish_events(producer, ["acme", "globex"], result)  # type: ignore[arg-type]
    topics = [t for t, _ in producer.messages]
    assert topics.count("tenant-acme.platform.pipeline.events.v1") == 2
    gold = json.loads(producer.messages[1][1])
    assert (gold["stage"], gold["status"], gold["detail"]["watermark"]) == (
        "gold",
        "curated",
        "2026-10-28T00:00:00+00:00",
    )
    target_dir = tmp_path / "surf_retail" / "postgres" / "target"
    target_dir.mkdir(parents=True)
    (target_dir / "manifest.json").write_text("{}")
    store = FakeStore()
    assert upload_artifacts(store, tmp_path, result) == ["dbt/surf_retail/postgres/manifest.json"]  # type: ignore[arg-type]
