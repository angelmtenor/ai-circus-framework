# Architecture guide - Read it bottom-up

Source: docs/architecture/README.md (this repository)

**Infrastructure.** Everything runs on one Kubernetes cluster: k3s, or k3d inside Docker on a laptop
(`make k3s-all`). Each service is a stateless container with requests, limits, probes and a non-root user. An
optional GPU node serves local models (vLLM) and deep-learning training. The LLM providers are outside the cluster.

**Data.** Where data lives and how it gets there:

- *Stores*: SeaweedFS (S3-compatible: datasets, models, documents, every key prefixed by tenant), PostgreSQL
  (tenants, entitlements, chat history, monitors' metadata), Qdrant (one vector collection per scenario and tenant),
  Valkey (cache and counters) and ClickHouse (traces, analytical marts).
- *Batch jobs*, run once per scenario or on demand: `etl-tabular` cleans a dataset, `training` trains and selects the
  model, `etl-vectorize` indexes documents, `dl-training` fine-tunes deep-learning models.
- *The data platform* (optional, in tiers): Kafka, `data-ingest` (contracts, quarantine, bronze), `data-transform`
  (dbt, Dagster), `data-api` (the governed door) and OpenMetadata.

**Consumption: AI, BI and GenAI.** What people use. `ui-react` is the web app. Behind it, **one service per kind
of scenario** serves every scenario of that kind: `prediction` and `assistant` for tabular ML, `rag-agent` for
document Q&A, `form-agent` for assisted forms, `dl-inference` for deep learning. Superset draws BI dashboards;
Open WebUI or LibreChat are optional chat apps that reach the platform through MCP.

**Gateways and governance.** The doors every request passes: Traefik (the only ingress), Keycloak (sign-in; a
tenant is a Keycloak Organization), `platform-registry` (scenarios, entitlements, the audit trail, the AI-BOM) and
`llm-gateway` (LiteLLM: every model call and every MCP tool call, with routing, limits, per-tenant budgets and the
local embedding model). The MCP server kit puts an existing API behind the MCP gateway with its own token audience.
`data-platform-manager` is the admin control plane behind the Platform page.

**Observability**, alongside everything: Langfuse records a trace per LLM call, MLflow every training run; the
optional lab bundle adds an OpenTelemetry collector, VictoriaMetrics, Loki, Tempo and Grafana
([ADR 0015](../adr/0015-observability-platform-contract.md)).
