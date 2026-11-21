# 🏄 Angel's Surf Co. — a governed data platform (`data_product`) - How it is built

Source: docs/scenarios/surf_retail.md (this repository)

- **Generic services, scenario-specific content.** `data-ingest`, `data-transform` and `data-api`
  contain no retail words. Everything specific lives in `scenarios/surf_retail/`: JSON Schema
  `contracts/`, a `simulator/` plugin, a `dbt/` project, `bi/dashboards.yaml` and the
  `data_product` block of `scenario.yaml`.
- **Switchable tiers**, never part of `make k3s-all`: core (Kafka, ingest, transform, API) ·
  analytics (ClickHouse, Dagster) · bi (Superset) · governance (OpenMetadata).
- Design, capability map and runbook: [docs/data-platform](../data-platform/README.md) and the
  [ADRs](../adr/README.md). Access policy and governance-ready contracts:
  [docs/governance](../governance/README.md).
