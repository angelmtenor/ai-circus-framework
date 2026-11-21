# Architecture details - Data Platform

Source: docs/architecture-details.md (this repository)

**Data Platform (`kind: data_product`, docs/data-platform/ + docs/adr/).** A data product is a
`data_product` block (schema `ai_circus_shared.data_product`) plus files next to its
scenario.yaml: JSON Schema `contracts/`, a `simulator/` plugin (`ai_circus_shared.data_simulator`:
produces only through Kafka / the landing bucket / the `data_sources` ERP database), a `dbt/`
project and `bi/dashboards.yaml`. Generic services: `data-ingest` (Kafka topics per tenant
`tenant-<org>.<topic>` → contract → bronze JSONB by key in database `data_platform` schema
`<slug>`, quarantine, file reconciliation, CDC from `data_sources`, hot-path minute aggregates,
Iceberg lake), `data-transform` (dbt → `<slug>_silver`/`<slug>_gold` on Postgres, then ClickHouse
copies silver and rebuilds gold; CronJob in core, Dagster in analytics; `bi_sync` for Superset),
`data-api` (the governed door: metrics, records + trail, sqlglot-rewritten SQL, lineage, SSE
pipeline events, simulator; access = `ai_circus_shared.data_policy`: org first, then the role's
row filter/masks; admin "view as" via `X-Data-View-As`), and `assistant`'s data tools
(`core/data_tools.py`). Four switchable tiers under `k8s/data-platform/{core,analytics,bi,governance}`
(`make k3s-data-*-up/down`, `k3s-data-showcase`), never part of `k3s-all`; first data product:
`surf_retail` — Angel's Surf Co., fictional (ui-react `DataProductView` + `Data*View.tsx`). A data product may
declare `apps` (`storefront`: a fictional web shop on its catalogue/recommendation datasets whose checkout is a hidden
simulator action, `StorefrontView.tsx` + `BoardArt.tsx`; `insights`: season heatmap + live forecast board,
`InsightsView.tsx`) and `ml_exports` (a gold feature mart → a `tabular_ml` scenario's raw CSV, written by
data-transform after each Postgres build; `make k3s-data-ml` trains them; the assistant gets `ml_predict`).
