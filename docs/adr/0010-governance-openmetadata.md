# 0010 — OpenMetadata for catalogue and lineage, ingestion as Jobs

**Context.** The showcase must include a real metadata platform. Options: OpenMetadata, DataHub,
Marquez. OpenMetadata's default deployment bundles Airflow for ingestion, plus MySQL/Postgres and
Elasticsearch/OpenSearch.

**Decision.** OpenMetadata server + OpenSearch (single node, small heap) in the governance tier,
metadata database in the existing Postgres. No Airflow: metadata ingestion runs as k8s Jobs using
the `openmetadata-ingestion` CLI with workflow YAMLs generated per data product (Postgres,
ClickHouse, Kafka with contract schemas, dbt manifest/catalog/run results, Superset, Dagster).

**Consequences.** One lineage graph from topic to dashboard. ~1 GiB saved by skipping Airflow;
re-ingestion is `make data-catalog-ingest` (or a Platform-view trigger), not a schedule.
