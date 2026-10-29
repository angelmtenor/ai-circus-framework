"""
- Title:    Dagster definitions — the analytics tier's orchestrator of every data product
- Author:   Angel Martinez-Tenor

`build_definitions(env)` turns each data product's dbt project into Dagster software-defined
assets (dagster-dbt: one asset per model, dbt tests as asset checks, the bronze tables as
upstream sources), one group per (data product, target), with:

- a **sensor** per data product polling the bronze watermark every minute — new data received
  since the last build requests a run of the Postgres assets (the same bronze → silver → gold
  build the core tier's CronJob runs, now with lineage, history and asset checks in Dagster);
- a **run-status sensor** chaining the ClickHouse build after each successful Postgres one
  (ClickHouse copies Postgres' silver, so it must never run first);
- a **nightly schedule** as a safety net.

Jobs use the in-process executor: one dbt invocation is the whole build, and a step subprocess
per run would load this code location a third time in the daemon's container.

Every build goes through the same record-keeping as the CronJob (`ops_transform_runs`,
artifacts for the catalogue, pipeline events): data-api, the UI and OpenMetadata cannot tell
which orchestrator ran it.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dagster import (
    AssetKey,
    AssetSelection,
    DagsterRunStatus,
    Definitions,
    RunRequest,
    RunsFilter,
    ScheduleDefinition,
    SensorEvaluationContext,
    SkipReason,
    define_asset_job,
    in_process_executor,
    run_status_sensor,
    sensor,
)
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets

from data_transform.core.products import DataProduct
from data_transform.core.runner import RunResult, _summarise, bronze_watermark, record, run_dbt

SENSOR_INTERVAL_SECONDS = 60


def _translator(product: DataProduct, target: str) -> DagsterDbtTranslator:
    group = f"{product.slug}_{target}"

    class Translator(DagsterDbtTranslator):
        """Asset keys `[slug, target, model]` (the two targets build same-named models); bronze
        sources are `[slug, bronze, table]`, and ClickHouse's `pg_silver` sources *are* the
        Postgres silver assets — so the graph shows ClickHouse copying from Postgres.
        """

        def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> AssetKey:
            name = dbt_resource_props["name"]
            if dbt_resource_props.get("resource_type") == "source":
                origin = "postgres" if dbt_resource_props.get("source_name") == "pg_silver" else "bronze"
                return AssetKey([product.slug, origin, name])
            return AssetKey([product.slug, target, name])

        def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
            return group

    return Translator()


def _product_assets(env: Any, product: DataProduct, target: str, after_run: Any) -> Any:
    artifacts = env.artifacts / product.slug / target
    # The manifest Dagster reads its graph from, parsed into a directory of this load only: the
    # webserver, the daemon and every run worker load this module (containers of one pod share
    # /tmp but not their PID namespaces), and two parses writing one manifest.json corrupt it.
    artifacts.mkdir(parents=True, exist_ok=True)
    parsed = Path(tempfile.mkdtemp(prefix="parse-", dir=artifacts))
    run_dbt(product, target, env.profiles, parsed, ["parse"])
    manifest = json.loads((parsed / "target" / "manifest.json").read_text())
    shutil.rmtree(parsed, ignore_errors=True)
    project_dir = product.scenario_dir / product.config.transform.dbt_project

    @dbt_assets(manifest=manifest, dagster_dbt_translator=_translator(product, target), name=f"{product.slug}_{target}")
    # `context` is left unannotated on purpose: Dagster inspects the annotation at runtime, and
    # this module's postponed annotations would hand it a string.
    def assets(context) -> Iterator[Any]:  # ruff: ignore[missing-type-function-argument]
        result = RunResult(slug=product.slug, target=target)
        result.watermark = bronze_watermark(env.engine, product)
        record(env.engine, product, result)
        dbt = DbtCliResource(project_dir=str(project_dir), profiles_dir=str(env.profiles), target=target)
        # target_path: the project itself lives on the read-only scenarios mount.
        invocation = dbt.cli(["build"], context=context, raise_on_error=False, target_path=artifacts / "target")
        yield from invocation.stream()
        run_results = invocation.get_artifact("run_results.json")
        _summarise(result, run_results)
        result.status = "success" if invocation.is_successful() else "failed"
        result.message = (
            f"{result.models_ok} models, {result.tests_passed} tests passed (Dagster run {context.run_id[:8]})"
        )
        if result.status == "success":
            run_dbt(product, target, env.profiles, artifacts, ["docs", "generate"])
        result.finished_at = datetime.now(UTC)
        record(env.engine, product, result)
        after_run(env, product, result)
        if result.status != "success":
            # dbt ran with raise_on_error=False so the run is recorded and published first; Dagster
            # must still show it failed (and the ClickHouse chain must not fire after it).
            raise RuntimeError(f"dbt build of {product.slug} on {target} failed: {result.message}")

    return assets


def build_definitions(env: Any, after_run: Any) -> Definitions:
    """Assets, jobs, sensors and schedules for every served data product."""
    all_assets, jobs, sensors, schedules = [], [], [], []
    for product in env.products.values():
        per_target = {target: _product_assets(env, product, target, after_run) for target in env.targets}
        all_assets.extend(per_target.values())
        pg_job = define_asset_job(
            f"{product.slug}_postgres_build",
            selection=AssetSelection.groups(f"{product.slug}_postgres"),
            executor_def=in_process_executor,
        )
        jobs.append(pg_job)
        schedules.append(ScheduleDefinition(name=f"{product.slug}_nightly", job=pg_job, cron_schedule="15 2 * * *"))
        sensors.append(_watermark_sensor(env, product, pg_job))
        if "clickhouse" in per_target:
            ch_job = define_asset_job(
                f"{product.slug}_clickhouse_build",
                selection=AssetSelection.groups(f"{product.slug}_clickhouse"),
                executor_def=in_process_executor,
            )
            jobs.append(ch_job)
            sensors.append(_chain_sensor(product, pg_job, ch_job))
    return Definitions(assets=all_assets, jobs=jobs, sensors=sensors, schedules=schedules)


def _watermark_sensor(env: Any, product: DataProduct, job: Any) -> Any:
    @sensor(
        name=f"{product.slug}_new_bronze",
        job=job,
        minimum_interval_seconds=SENSOR_INTERVAL_SECONDS,
        default_status=_running(),
    )
    def new_bronze(context: SensorEvaluationContext) -> Any:
        watermark = bronze_watermark(env.engine, product)
        if watermark is None:
            return SkipReason("bronze is empty")
        stamp = watermark.isoformat()
        if context.cursor and context.cursor >= stamp:
            return SkipReason(f"nothing received since {context.cursor}")
        # One build pending at a time: while data streams in (a history load) a run per new
        # watermark would pile up; the cursor stays put, so the next tick builds the newest data.
        pending = context.instance.get_runs(
            RunsFilter(
                job_name=job.name,
                statuses=[
                    DagsterRunStatus.QUEUED,
                    DagsterRunStatus.STARTING,
                    DagsterRunStatus.STARTED,
                    DagsterRunStatus.NOT_STARTED,
                ],
            ),
            limit=1,
        )
        if pending:
            return SkipReason("a build of this data product is already queued or running")
        context.update_cursor(stamp)
        return RunRequest(run_key=f"{product.slug}:{stamp}", tags={"watermark": stamp})

    return new_bronze


def _chain_sensor(product: DataProduct, upstream: Any, downstream: Any) -> Any:
    @run_status_sensor(
        name=f"{product.slug}_clickhouse_after_postgres",
        run_status=DagsterRunStatus.SUCCESS,
        monitored_jobs=[upstream],
        request_job=downstream,
        default_status=_running(),
    )
    def chain(context: Any) -> Any:
        return RunRequest(
            run_key=f"after:{context.dagster_run.run_id}", tags={"upstream_run": context.dagster_run.run_id}
        )

    return chain


def _running() -> Any:
    from dagster import DefaultSensorStatus

    return DefaultSensorStatus.RUNNING


def manifest_summary(path: Path) -> dict[str, int]:
    """Counts of the models and tests in a manifest (for the logs)."""
    nodes = json.loads(path.read_text()).get("nodes", {}).values()
    return {kind: sum(1 for n in nodes if n.get("resource_type") == kind) for kind in ("model", "test")}
