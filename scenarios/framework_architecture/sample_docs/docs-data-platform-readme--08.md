# Data Platform — an open-source, scenario-driven data platform tier - Milestones

Source: docs/data-platform/README.md (this repository)

| | Scope | Done when |
|---|---|---|
| M0 | This document, ADRs, `data_product` kind in the schema + registry | Schema tests green; registry seeds the kind |
| M1 | Contracts, retail simulator, topics/buckets/databases bootstrap | Simulator backfill + live produce valid messages |
| M2 | `data-ingest` (stream, batch + reconciliation, CDC, quarantine, archive, hot aggregates) | Replay keeps counts; bad record quarantined; legacy gap flagged |
| M3 | dbt project (staging, SCD2 dims, facts, marts, tests) + `data-transform` Job; `data-api` | `dbt build` green on DuckDB and Postgres; receipt reaches gold |
| M4 | Analytics tier: ClickHouse marts + hot path, Dagster | Same marts on ClickHouse; sensor-driven runs |
| M5 | BI tier: Superset with dashboards as code | Dashboards imported by `make` |
| M6 | Governance tier: OpenMetadata + ingestion Jobs | Lineage topic → dashboard visible |
| M7 | ui-react DataProductView (live flow, simulate, explore) + assistant data tools | Storyline runs from the browser |
| M8 | Hardening: verify targets, limits tuned, docs, screenshots | Clean cluster → demo in one documented sequence |
