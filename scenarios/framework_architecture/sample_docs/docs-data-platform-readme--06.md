# Data Platform — an open-source, scenario-driven data platform tier - How a data product is declared

Source: docs/data-platform/README.md (this repository)

```text
scenarios/<slug>/
  scenario.yaml        # kind: data_product — sources, bronze, transform, metrics, access, serving, ui
  contracts/           # JSON Schema of every event / file
  simulator/           # deterministic generator (backfill + live + anomalies), plugin API below
  dbt/                 # silver/gold models + tests (targets: postgres, clickhouse, duckdb)
  bi/dashboards.yaml   # Superset dashboards as code (bi tier)
```

`scenario.yaml`'s `data_product` block (schema: `ai_circus_shared.data_product`) declares
sources (kafka topics with JSON Schema contracts and record rules, file deliveries with their
control files, CDC tables), the hot-path aggregates, the curated datasets with freshness SLOs,
the governed metrics, record entities, access roles (row filter, hidden fields, layers, SQL),
simulator actions, the native dashboard's KPIs and panels, and the stage labels of the live
flow.

Adding a data product is those files plus a `platform-registry` restart — no new service or UI
code. If a second data product ever needs a change inside `services/data-*`, that change must be
generic (it is a bug in the platform, not a feature of the scenario).
