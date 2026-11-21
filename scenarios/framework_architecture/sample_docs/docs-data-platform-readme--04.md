# Data Platform — an open-source, scenario-driven data platform tier - Capability map — what exists, what is new

Source: docs/data-platform/README.md (this repository)

Verified against `develop` at v0.30.2, before this tier landed (the proposal's §3.1 was an assumption list); the *Gap / change* column is what v0.31.0 added.

| Capability | Already in the repo | Gap / change |
|---|---|---|
| Object storage | SeaweedFS (`ai_circus_shared.storage`) | New bucket `ai-circus-data` (landing, lake) |
| Relational + document | Postgres 16, JSONB `document_store` | New database `data_platform`, a schema per data product |
| Event backbone | Kafka 3.9 KRaft (`k8s/data-platform/kafka.yaml`, `ai_circus_shared.events`) | Moved under the core tier (`core/kafka.yaml`); a topic per contract and tenant |
| CDC | Postgres logical replication, `ai_circus_shared.cdc` (test_decoding, polling) | Reused by `data-ingest` for the data product's operational tables |
| Lakehouse | PyIceberg SQL catalog on SeaweedFS (`data-platform-manager/core/lakehouse.py`) | `data-ingest`'s lake archiver: one Iceberg table per source, day-partitioned |
| Federation / semantic | DuckDB over Iceberg + Postgres (`data-platform-manager/core/semantic.py`) | Metrics become scenario-declared (`metrics:`), served by `data-api` |
| Analytical engine | ClickHouse 26.4 (Langfuse backend only) | Own `data_platform` database + user, analytics tier |
| Transformation | — | dbt-core project per data product (`scenarios/<slug>/dbt/`) |
| Orchestration | k8s Jobs triggered by data-platform-manager | Core: dbt Job/CronJob; analytics: Dagster |
| Contracts | Pydantic scenario schema only | JSON Schema contracts per source (`ai_circus_shared.contracts`) |
| Data quality | — | Record rules + quarantine at ingest; dbt tests; reconciliation |
| Catalogue + lineage | — | dbt docs (core), Dagster (analytics), OpenMetadata (governance) |
| BI | ui-react Data tab (Plotly) | Native live tab (core) + Superset (bi tier) |
| Data API | per-kind services | `data-api` (records, search, metrics, governed SQL, SSE, MCP) |
| Assistant over data | `assistant` (tabular_ml tools) | Data tools bound for `data_product` scenarios |
| Identity / tenancy | Keycloak orgs, `resolve_caller_identity`, entitlements | Row filters + column masks from roles/claims (`ai_circus_shared.data_policy`); token claims on `Identity.attributes` |
| Operations | data-platform-manager status/resources/workloads/roadmap | Rows for the new components; data tiers in `OPTIONAL_SERVICES` |
| Scenario kind | 4 kinds | 5th kind `data_product` |
