# Components and request flows - Data

Source: docs/architecture/components.md (this repository)

Where data lives and how it gets there: object storage, databases and vectors, the jobs that clean data, train models and index documents, and the optional streaming data platform.

| Component | What it does | Technology | How to switch it on | ADR |
|---|---|---|---|---|
| **SeaweedFS** | Datasets, trained models, documents, MLflow artifacts, the data platform's landing zone and Iceberg lake, every key prefixed by tenant. | S3-compatible object storage | always on | — |
| **PostgreSQL** | Keycloak, tenants and entitlements, chat history, form submissions, Langfuse and MLflow metadata, and the data platform's bronze and marts. | PostgreSQL | always on | — |
| **Valkey** | Cache: sessions, rate-limit and budget counters of llm-gateway, Langfuse's queue. | Valkey (Redis protocol) | always on | — |
| **Qdrant** | One vector collection per scenario and tenant: the passages RAG and form assistants search. | Qdrant vector database | always on | — |
| **ClickHouse** | Langfuse's trace store, and the data platform's analytical marts on the analytics tier. | ClickHouse | always on | — |
| **etl-tabular** | Cleans each tabular scenario's dataset, applies its business rules and data-quality policy, and writes the processed copy. | k8s Job · pandas, pyarrow | make k3s-pipeline | — |
| **training** | Trains every candidate, picks the model by its metric (simpler wins within a margin), writes the model, its card and out-of-fold scores. | k8s Job · scikit-learn, LightGBM, SHAP | make k3s-pipeline | — |
| **etl-vectorize** | Chunks each scenario's documents, embeds them through llm-gateway and writes them to the tenant's Qdrant collection. | k8s Job · chunking, embeddings | make k3s-vectorize | — |
| **dl-training** | Fine-tunes the deep-learning scenarios (NLP, computer vision, anomaly detection) and exports ONNX models. | k8s Job or host GPU · PyTorch, exported to ONNX | make k3s-dl-train SCENARIO=<slug> | — |
| **Kafka** | The event backbone of a data product: a topic per contract and tenant, fed by its simulator and by change capture. | Kafka 3.9 KRaft | make k3s-data-core-up | [0003](../adr/0003-event-backbone-and-contracts.md) |
| **data-ingest** | Validates every record against its contract, upserts it idempotently into bronze, quarantines what fails, archives to the Iceberg lake and keeps the live aggregates. | FastAPI · consumers | make k3s-data-core-up | [0004](../adr/0004-bronze-and-lakehouse.md), [0012](../adr/0012-hot-path.md) |
| **data-transform** | Builds silver and gold from bronze with tested dbt models; the marts the dashboards and the API read. | dbt · CronJob | make k3s-data-core-up | [0006](../adr/0006-transformation-and-orchestration.md) |
| **Dagster** | Orchestrates the transformations with sensors, lineage and checks on the analytics tier. | Dagster | make k3s-data-analytics-up | [0006](../adr/0006-transformation-and-orchestration.md) |
| **data-api** | The governed door to a data product: records, search, metrics, read-only SQL and live events, filtered by tenant first, then the role's rows and masks. | FastAPI · REST, SSE, MCP | make k3s-data-core-up | [0007](../adr/0007-serving-api-assistant-mcp.md), [0008](../adr/0008-tenancy-and-access-policy.md), [0019](../adr/0019-mcp-doors-and-tool-policy.md) |
| **OpenMetadata** | The data catalogue: owners, lineage and tests of every dataset, kept in sync by a Job. | OpenMetadata · Elasticsearch | make k3s-data-governance-up | [0010](../adr/0010-governance-openmetadata.md) |
| **crm-api** | An example business API (customers of the shops) that an MCP server is put in front of, with its own token audience. | FastAPI | make k3s-mcp-examples-up | [0022](../adr/0022-mcp-server-kit-and-per-api-tokens.md) |
