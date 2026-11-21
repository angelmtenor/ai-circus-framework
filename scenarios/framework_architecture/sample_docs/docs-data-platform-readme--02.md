# Data Platform — an open-source, scenario-driven data platform tier - Architecture

Source: docs/data-platform/README.md (this repository)

```mermaid
flowchart LR
  subgraph SRC[Sources — scenario simulator]
    EV[Event producers<br/>tills, web orders]
    FL[Daily files<br/>+ control totals]
    OP[(Operational DB<br/>ERP tables)]
  end
  subgraph CAP[Capture]
    K[(Kafka)]
    CDC[CDC<br/>logical replication]
    LND[(SeaweedFS<br/>landing/)]
  end
  subgraph ING[data-ingest]
    V[contract validation<br/>idempotent upsert<br/>quarantine]
    HP[hot aggregates]
    AR[Iceberg archive]
  end
  subgraph STORE[Storage]
    BR[(Postgres bronze<br/>JSONB by key)]
    LK[(Iceberg lake<br/>on SeaweedFS)]
    WH[(Marts<br/>Postgres · ClickHouse)]
  end
  subgraph TR[data-transform]
    DBT[dbt build<br/>silver → gold + tests]
    DG[Dagster<br/>sensors · lineage · checks]
  end
  subgraph SRV[Serve]
    API[data-api<br/>records · search · metrics · SQL · SSE · MCP]
    AS[assistant<br/>data tools]
    BI[Superset]
    OM[OpenMetadata]
  end
  UI[ui-react<br/>DataProductView]

EV --> K
  OP --> CDC --> K
  FL --> LND
  K --> V --> BR
  V --> HP
  V --> AR --> LK
  LND --> V
  BR --> DBT --> WH
  DG --> DBT
  WH --> API
  BR --> API
  API --> AS
  WH --> BI
  BR & WH & K & LK -. metadata .-> OM
  API --> UI
  AS --> UI
```

Cross-cutting, reused as-is: Keycloak (identity, tenant = Keycloak Organization),
platform-registry (scenarios + entitlements), llm-gateway (every LLM call), Langfuse (assistant
traces), data-platform-manager (admin Platform view), Traefik (ingress).
