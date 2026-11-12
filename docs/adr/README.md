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
| [0018](0018-several-models-per-scenario.md) | One scenario can hold several models (`extra_models:`), each addressed as `slug~id` with its own dataset and bucket; one UI tab per model |
| [0019](0019-mcp-doors-and-tool-policy.md) | MCP is a door, not a kind: first a read-only `/mcp/{scenario_slug}` on `rag-agent` (list, search, read documents), existing identity; OAuth, `data-api`, models as tools and tool policy come later |
| [0020](0020-mcp-gateway-without-enterprise.md) | llm-gateway is also the MCP gateway: `/mcp/{slug},{slug}` in front of the doors, every entry `true_passthrough` (the caller's token goes through, the doors decide), open-source LiteLLM features only |
| [0021](0021-chat-ui-with-real-keycloak-sign-in.md) | Optional chat UI (Open WebUI first, LibreChat next) with real Keycloak sign-in; each user's token reaches the MCP doors through the gateway; the realm bootstrap issues `sub`, roles and the tenant at last |
| [0022](0022-mcp-server-kit-and-per-api-tokens.md) | `libs/mcp-kit`: an MCP server in front of an existing API inherits token validation, the access policy, the per-API token exchange (Keycloak, one audience per server) and audit; example `crm-mcp` + `crm-api` |
| [0023](0023-observability-of-the-mcp-path.md) | Keep the ADR 0015 stack; mcp-kit reports calls, refusals and per-API tokens as metrics and trace-linked audit lines; one trace from the MCP server inwards, the MCP hop correlated until its clients propagate trace context |
| [0024](0024-one-mcp-catalog.md) | `mcp/catalog.yaml` is the single source of truth for the MCP path: a generator writes the gateway routes, chat UI connections, kit servers' policies and Keycloak exchange clients, CI fails on drift; an optional local portal edits it |
