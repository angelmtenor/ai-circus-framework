# 0006 — dbt-core for transformations; Job in core, Dagster in analytics

**Context.** Transformations must be versioned SQL with tests, docs and lineage. Orchestration
options: Dagster, Airflow, Prefect, plain CronJobs.

**Decision.** dbt-core with `dbt-postgres`, `dbt-clickhouse` and `dbt-duckdb`. One image,
`data-transform`, carries dbt plus Dagster. In the core tier it runs as a k8s Job (one-shot) and a
CronJob (every few minutes), launched like `etl-tabular`; in the analytics tier the same image
runs Dagster (webserver + daemon in one pod, instance storage in Postgres) with one asset group
per data product (`dagster-dbt`), a sensor on bronze watermarks and a nightly schedule.

**Consequences.** Core stays cheap (no long-running orchestrator). Dagster adds lineage, asset
checks and run history when memory allows. Airflow was rejected (heavier, and OpenMetadata no
longer needs it, see 0010).
