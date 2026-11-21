# Data Platform — an open-source, scenario-driven data platform tier - Tiers — one cluster, switched on by layer

Source: docs/data-platform/README.md (this repository)

The whole data tier is optional and **off by default**: `make k3s-all` never starts it. Each
tier is a kustomize overlay under `k8s/data-platform/<tier>/` with its own `make` target, so a
16 GB laptop runs the layers a demo needs instead of all of them.

| Tier | Make target | Components | Capabilities shown |
|---|---|---|---|
| **core** | `k3s-data-core-up` | Kafka · `data-ingest` · `data-api` · `data-transform` (dbt as a Job) · Postgres bronze/silver/gold · Iceberg on SeaweedFS | Event capture, CDC, file landing + reconciliation, contracts, quarantine, lakehouse, transformations as code, data quality, governed API, row-level access, pseudonymisation, assistant over data, live flow |
| **analytics** | `k3s-data-analytics-up` | ClickHouse marts + hot path (shared ClickHouse instance, own database) · Dagster webserver + daemon | Columnar marts, second-level hot aggregates, orchestration with sensors, asset lineage and checks |
| **bi** | `k3s-data-bi-up` | Apache Superset | Dashboards versioned as code, role-scoped SQL access |
| **governance** | `k3s-data-governance-up` | OpenMetadata + OpenSearch (metadata ingestion as Jobs, no Airflow) | Catalogue, end-to-end lineage (topic → bronze → dbt → marts → dashboard), glossary, quality results, ownership |
| demo (no governance) | `k3s-data-demo` | core + analytics + bi with every scenario kind running; MLflow, voice and governance off | The usual multi-scenario demo including data; `k3s-data-demo-off` undoes it |
| everything | `k3s-data-showcase` | all four tiers, AI-only extras scaled to zero | The full storyline on one laptop |

`k3s-data-showcase` follows `k3s-demo`'s pattern: it frees memory by scaling to zero what a
data demo does not need (mlflow, agui-voice, dl-inference, Langfuse web/worker), then applies
every tier. `k3s-data-showcase-off` restores them.
