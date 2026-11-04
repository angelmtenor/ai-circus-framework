# Architecture Decision Records

Short records of decisions that are not obvious from the code. Format: context, decision,
consequences. Status is `accepted` unless marked otherwise; a superseded ADR stays, with a
pointer to its replacement.

| ADR | Decision |
|---|---|
| [0001](0001-data-platform-in-repo-same-cluster.md) | Data Platform lives in this repo and on the same k3s cluster, as switchable tiers |
| [0002](0002-data-product-scenario-kind.md) | `data_product` is a fifth scenario kind, served by generic `data-*` services |
| [0003](0003-event-backbone-and-contracts.md) | Kafka (existing) as the event backbone, JSON Schema contracts, a topic per tenant |
| [0004](0004-bronze-and-lakehouse.md) | Bronze = Postgres JSONB record store + Iceberg archive on SeaweedFS |
| [0005](0005-warehouse-engines.md) | Marts on Postgres (core) and ClickHouse (analytics, shared instance) |
| [0006](0006-transformation-and-orchestration.md) | dbt-core for transformations; Job in core, Dagster in analytics |
| [0007](0007-serving-api-assistant-mcp.md) | One `data-api` door set: REST, SSE, governed SQL, MCP; assistant data tools |
| [0008](0008-tenancy-and-access-policy.md) | Tenancy by `org_id` everywhere + scenario-declared row filters / column masks |
| [0009](0009-bi-superset.md) | Apache Superset as BI, dashboards as code |
| [0010](0010-governance-openmetadata.md) | OpenMetadata for catalogue + lineage, ingestion as Jobs (no Airflow) |
| [0011](0011-governance-ready-by-standards.md) | Governance-ready through open standards (JSON Schema, CloudEvents, OTel, CycloneDX, OPA-compatible), not a product; everything off by default |
| [0012](0012-hot-path.md) | Hot path: ingest-side minute aggregates, in every tier |
| [0013](0013-simulator-as-scenario-plugin.md) | Data product simulators are scenario-shipped Python plugins (numbered 0011 until the governance ADR took that number) |
| [0014](0014-charting-and-bi-strategy.md) | Plotly stays for the chart builder, large scatters and 3-D; SVG primitives and `d3-geo` own small charts and maps; Superset for BI, Power BI by contract |
| [0015](0015-observability-platform-contract.md) | The metrics/logs/traces stack is a platform prerequisite; each service honours a contract: `/metrics`, OTLP traces when an endpoint is set, JSON logs with trace ids, no content |
| [0016](0016-time-series-forecasting.md) | Forecasting is a third tabular task (`task_type: forecast` + `time_series:`), backtested by MASE under the Green Code rule, with a `forecast_board` tab |
| [0017](0017-vllm-alongside-ollama.md) | vLLM is an optional GPU backend alongside Ollama, reached only through llm-gateway |
