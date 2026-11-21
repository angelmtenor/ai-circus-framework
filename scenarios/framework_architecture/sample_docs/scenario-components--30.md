# What each scenario is built from - Angel's Surf Co. — Data Platform (surf_retail, data_product)

Source: generated from every scenario.yaml (this repository)

Angel's Surf Co. (fictional) sells surfboards, SUP and kite gear through 24 shops on the coasts of Spain, France, Italy, Greece, Cyprus and Malta, and a web shop. Five years of history arrive as migration files, tills stream receipts through Kafka, the Greek franchise's old tills send daily files reconciled against control totals, and the ERP is captured by change-data-capture. Every record lands raw in bronze, dbt builds tested silver and gold marts, and one governed API serves live tiles, dashboards, a metadata catalogue and an assistant that answers with numbers — store managers only ever see their own shop.

Components it uses:
- Kubernetes (k3s) (infra): Runs every pod this scenario needs.
- LLM providers (infra): The active LLM answers the chat.
- SeaweedFS (data): The landing files and the Iceberg lake.
- PostgreSQL (data): Holds tenants, entitlements and chat history.
- Valkey (data): The gateway's rate-limit and budget counters.
- ClickHouse (data): The analytical marts on the analytics tier.
- Kafka (data): The events of its sources, a topic per contract and tenant.
- data-ingest (data): Validates every record against its contract into bronze, quarantines what fails.
- data-transform (data): Builds silver and gold with tested dbt models.
- Dagster (data): Orchestrates the transformations on the analytics tier.
- data-api (data): The governed door: metrics, records and SQL filtered by tenant and role.
- OpenMetadata (data): Its datasets' owners and lineage on the governance tier.
- ui-react (consumption): Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.
- assistant (consumption): The chat: answers from the governed data tools.
- Superset (consumption): Its dashboards, imported from the scenario's YAML.
- Open WebUI · LibreChat (consumption): Optional: the same tools from LibreChat or Open WebUI, with the user's own token.
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call.
- llm-gateway (gateway): Every model call of the chat goes through it, with the tenant's limits and budget. The MCP gateway routes its read-only door to MCP clients.
- Langfuse (observability): A trace per LLM call, by tenant and scenario.

Request flows it follows: A record through the data platform.
