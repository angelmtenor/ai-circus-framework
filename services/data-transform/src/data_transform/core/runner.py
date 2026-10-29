"""
- Title:    Run a data product's dbt project and record the run
- Author:   Angel Martinez-Tenor

One `transform(product, target)` call = one `dbt build` (models + tests, in DAG order) of the
data product's project, then `dbt docs generate` for the catalogue. Around it:

- the pseudonymisation function dbt's `pseudonymise()` macro calls is (re)installed with the
  key read from the environment into a private table — the key never appears in compiled SQL,
  dbt logs or the artifacts the catalogue ingests;
- the bronze **watermark** is taken *before* the build (the latest received_at, held back a few
  seconds for transactions still committing): after a successful run, everything received up
  to it is in gold — what data-api's processing trail and freshness report;
- the outcome goes to `<slug>.ops_transform_runs`, the artifacts to the object store
  (`tenant-platform/dbt/<slug>/<target>/`) and a pipeline event to every tenant with data.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from ai_circus_shared.data_product import TRANSFORM_RUNS_TABLE, bronze_table
from sqlalchemy import Engine, text

from data_transform.core.logger import get_logger
from data_transform.core.products import DataProduct

logger = get_logger(__name__)

PRIVATE_SCHEMA = "data_platform_private"
WATERMARK_HOLDBACK = "5 seconds"
PLATFORM_ORG = "platform"  # object-store prefix of platform metadata (not a tenant's data)


@dataclass
class RunResult:
    """One build of one data product on one target, as recorded in ops_transform_runs."""

    slug: str
    target: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "running"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    watermark: datetime | None = None
    models_ok: int = 0
    models_failed: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    message: str = ""
    failures: list[dict[str, Any]] = field(default_factory=list)


def install_pseudonymisation(engine: Engine, key: str) -> None:
    """HMAC-SHA256 with the key held in a private table (pgcrypto)."""
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('data_platform_private'))"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PRIVATE_SCHEMA}"))
        conn.execute(text(f"REVOKE ALL ON SCHEMA {PRIVATE_SCHEMA} FROM PUBLIC"))
        conn.execute(
            text(f"CREATE TABLE IF NOT EXISTS {PRIVATE_SCHEMA}.keys (name text PRIMARY KEY, value text NOT NULL)")
        )
        conn.execute(
            text(
                f"INSERT INTO {PRIVATE_SCHEMA}.keys VALUES ('pseudonym', :k) "
                "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key},
        )
        conn.execute(
            text(f"""
            CREATE OR REPLACE FUNCTION {PRIVATE_SCHEMA}.pseudonymise(v text) RETURNS text
            LANGUAGE sql STABLE PARALLEL SAFE AS $$
                SELECT encode(public.hmac(v, (SELECT value FROM {PRIVATE_SCHEMA}.keys WHERE name = 'pseudonym'),
                                          'sha256'), 'hex')
            $$""")
        )


def ensure_runs_table(engine: Engine, product: DataProduct) -> None:
    """Create the data product's run log (and its schema, before data-ingest ever ran)."""
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{product.slug}"'))
        conn.execute(
            text(f"""CREATE TABLE IF NOT EXISTS "{product.slug}"."{TRANSFORM_RUNS_TABLE}" (
            run_id text PRIMARY KEY, target text NOT NULL, status text NOT NULL, started_at timestamptz NOT NULL,
            finished_at timestamptz, watermark timestamptz, engine text, models_ok int, models_failed int,
            tests_passed int, tests_failed int, message text, failures jsonb)""")
        )


def bronze_watermark(engine: Engine, product: DataProduct) -> datetime | None:
    """Latest received_at across the data product's bronze tables, held back for in-flight commits."""
    parts = [
        f'SELECT max(received_at) AS m FROM "{product.slug}"."{bronze_table(s.id)}"'
        for s in product.config.kafka_sources()
    ]
    if not parts:
        return None
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT least(max(m), now() - interval '{WATERMARK_HOLDBACK}') FROM ({' UNION ALL '.join(parts)}) u")
        ).scalar()


def tenants_with_data(engine: Engine, product: DataProduct) -> list[str]:
    """The tenants that have records in this data product (who to send the run's events)."""
    sources = product.config.kafka_sources()
    if not sources:
        return []
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(f'SELECT DISTINCT org_id FROM "{product.slug}"."{bronze_table(sources[0].id)}"')
            ).scalars()
        )


def write_profiles(
    directory: Path,
    *,
    host: str,
    port: str,
    user: str,
    database: str,
    threads: int,
    clickhouse_host: str | None,
    clickhouse_user: str | None,
) -> Path:
    """profiles.yml for profile `data_platform`; passwords are read by dbt from env vars, so
    they are never written to disk.
    """
    outputs: dict[str, Any] = {
        "postgres": {
            "type": "postgres",
            "host": host,
            "port": int(port),
            "user": user,
            "password": "{{ env_var('DBT_POSTGRES_PASSWORD') }}",
            "dbname": database,
            "schema": "public",
            "threads": threads,
            "connect_timeout": 10,
        },
    }
    if clickhouse_host:
        outputs["clickhouse"] = {
            "type": "clickhouse",
            "host": clickhouse_host,
            "port": 8123,
            "user": clickhouse_user or "default",
            "password": "{{ env_var('DBT_CLICKHOUSE_PASSWORD', '') }}",
            "schema": "default",
            "threads": threads,
            # The shared (Langfuse-tuned) instance caps a query at 256 MB; copying silver through
            # the PostgreSQL engine needs more, in small blocks.
            "custom_settings": {
                "join_use_nulls": 1,
                "max_memory_usage": 629145600,
                "max_block_size": 16384,
                "max_threads": 2,
                "max_insert_threads": 1,
            },
        }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "profiles.yml"
    path.write_text(str(yaml.safe_dump({"data_platform": {"target": "postgres", "outputs": outputs}})))
    return path


def _summarise(result: RunResult, run_results: dict[str, Any]) -> None:
    for item in run_results.get("results", []):
        uid, status = item.get("unique_id", ""), item.get("status")
        if uid.startswith("model."):
            if status == "success":
                result.models_ok += 1
            else:
                result.models_failed += 1
                result.failures.append({"node": uid, "status": status, "message": (item.get("message") or "")[:300]})
        elif uid.startswith("test."):
            if status == "pass":
                result.tests_passed += 1
            elif status in ("fail", "error", "warn"):
                result.tests_failed += status != "warn"
                result.failures.append({"node": uid, "status": status, "message": (item.get("message") or "")[:300]})


def run_dbt(
    product: DataProduct, target: str, profiles_dir: Path, artifacts: Path, command: list[str]
) -> tuple[bool, dict[str, Any]]:
    """Invoke dbt in-process; returns (success, run_results.json)."""
    from dbt.cli.main import dbtRunner  # heavy import: only when a run actually happens

    target_path, log_path = artifacts / "target", artifacts / "logs"
    args = [
        *command,
        "--project-dir",
        str(product.scenario_dir / product.config.transform.dbt_project),
        "--profiles-dir",
        str(profiles_dir),
        "--target",
        target,
        "--target-path",
        str(target_path),
        "--log-path",
        str(log_path),
        "--no-use-colors",
    ]
    outcome = dbtRunner().invoke(args)
    results_file = target_path / "run_results.json"
    run_results = json.loads(results_file.read_text()) if results_file.exists() else {}
    return bool(outcome.success), run_results


def record(engine: Engine, product: DataProduct, result: RunResult) -> None:
    """Upsert the run in ops_transform_runs and keep only the latest 500."""
    with engine.begin() as conn:
        conn.execute(
            text(f"""INSERT INTO "{product.slug}"."{TRANSFORM_RUNS_TABLE}" (run_id, target, status, started_at,
                finished_at, watermark, engine, models_ok, models_failed, tests_passed, tests_failed, message, failures)
            VALUES (:run_id, :target, :status, :started_at, :finished_at, :watermark, :target, :models_ok,
                    :models_failed, :tests_passed, :tests_failed, :message, CAST(:failures AS jsonb))
            ON CONFLICT (run_id) DO UPDATE SET status = EXCLUDED.status, finished_at = EXCLUDED.finished_at,
                models_ok = EXCLUDED.models_ok, models_failed = EXCLUDED.models_failed,
                tests_passed = EXCLUDED.tests_passed, tests_failed = EXCLUDED.tests_failed,
                message = EXCLUDED.message, failures = EXCLUDED.failures"""),
            {
                **{k: v for k, v in result.__dict__.items() if k not in ("slug", "failures")},
                "failures": json.dumps(result.failures),
            },
        )
        # Bounded history: the last 500 runs per data product are plenty for the views.
        conn.execute(
            text(f"""DELETE FROM "{product.slug}"."{TRANSFORM_RUNS_TABLE}" WHERE run_id IN (
            SELECT run_id FROM "{product.slug}"."{TRANSFORM_RUNS_TABLE}" ORDER BY started_at DESC OFFSET 500)""")
        )


def transform(
    engine: Engine, product: DataProduct, target: str, profiles_dir: Path, artifacts_root: Path, *, docs: bool = True
) -> RunResult:
    """One build of one data product on one target, serialised across processes by an
    advisory lock (a CronJob tick never overlaps a still-running build).
    """
    result = RunResult(slug=product.slug, target=target)
    artifacts = artifacts_root / product.slug / target
    with engine.connect() as lock_conn:
        got = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": f"transform:{product.slug}:{target}"}
        ).scalar()
        if not got:
            result.status, result.message = "skipped", "another build of this data product is running"
            return result
        try:
            result.watermark = bronze_watermark(engine, product)
            record(engine, product, result)
            started = time.monotonic()
            ok, run_results = run_dbt(product, target, profiles_dir, artifacts, ["build"])
            _summarise(result, run_results)
            if docs and ok:
                run_dbt(product, target, profiles_dir, artifacts, ["docs", "generate"])
            result.status = "success" if ok else "failed"
            result.message = (
                f"{result.models_ok} models, {result.tests_passed} tests passed in {time.monotonic() - started:.0f}s"
            )
            if result.failures:
                result.message += f"; {len(result.failures)} failure(s)"
        except Exception as exc:
            logger.exception("Transform of {} on {} crashed", product.slug, target)
            result.status, result.message = "failed", str(exc)[:500]
        finally:
            result.finished_at = datetime.now(UTC)
            record(engine, product, result)
            lock_conn.execute(
                text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": f"transform:{product.slug}:{target}"}
            )
    return result


def env_for_dbt(postgres_password: str, clickhouse_password: str | None) -> None:
    """Expose the passwords to dbt through the env vars profiles.yml reads (never written to disk)."""
    os.environ["DBT_POSTGRES_PASSWORD"] = postgres_password
    os.environ["DBT_CLICKHOUSE_PASSWORD"] = clickhouse_password or ""
    os.environ.setdefault("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
