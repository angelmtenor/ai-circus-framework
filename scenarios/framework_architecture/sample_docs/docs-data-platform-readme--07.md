# Data Platform — an open-source, scenario-driven data platform tier - Memory budget (measured on the reference laptop, WSL2 with 11 GiB)

Source: docs/data-platform/README.md (this repository)

Base framework when measured (v0.30.2, all 17 pods): **≈ 5.9 GiB** working set, node ≈ 7.2 GiB.
Per-tier numbers are filled in by each milestone's verification (`kubectl top`, peak under the
demo storyline), never copied from the proposal's estimates.

| Tier | Limits (sum) | Measured | Notes |
|---|---|---|---|
| core | 2.0 GiB (+ 768 MiB transform Job) | ≈ 0.8 GiB | kafka 460 MiB, data-ingest 190 MiB, data-api 140 MiB; the CronJob is suspended while Dagster owns transforms |
| analytics | 2.1 GiB | ≈ 1.5 GiB | Dagster daemon ≈ 1.06 GiB right after a dbt build (limit 1.5 GiB), webserver ≈ 420 MiB; ClickHouse is the shared Langfuse instance (178 MiB with the marts loaded) |
| bi | 1 GiB | ≈ 0.2 GiB | Superset idle, three dashboards synced |
| governance | 2 GiB (+ 1 GiB sync Job) | ≈ 1.8 GiB | OpenMetadata 845 MiB, om-search (Elasticsearch) 960 MiB; the heaviest tier — switch it on for the catalogue part of the demo |

Measured on 29 Oct 2026 with every tier up and the AI-only pods (langfuse web, mlflow,
agui-voice, prediction, rag-agent, form-agent, qdrant) scaled to 0: node ≈ 9.2 GiB used of 11.7.

Re-checked on 30 Oct 2026 (v0.33.1, after the governance-ready releases) with every tier up and prediction + assistant
running (rag-agent, form-agent, qdrant, langfuse, mlflow, agui-voice at 0): core ≈ 0.77 GiB (kafka 465 MiB,
data-ingest 153, data-api 150), analytics ≈ 1.3 GiB (Dagster 1.16 GiB, both containers; ClickHouse 159 MiB), bi ≈ 0.23 GiB, governance ≈ 1.8 GiB (OpenMetadata 829 MiB, om-search 966 MiB); node 8.7 GiB used of 11.7
(74%).
The images of the AI services (llm-gateway, prediction, …) were not rebuilt for this check, so it says nothing about the memory cost of the v0.32–v0.33 governance code in them.
