# Components and request flows - Request flows

Source: docs/architecture/components.md (this repository)

### A prediction, explained

A user scores one record in a model tab.

1. **ui-react → Traefik**: POST /predict/{scenario} with the user's token
2. **Traefik → prediction**: routed by hostname
3. **prediction → Keycloak**: token checked (signature, organization)
4. **prediction → platform-registry**: is this tenant entitled to this scenario?
5. **prediction → SeaweedFS**: the tenant's model, already loaded at start-up
6. **prediction → ui-react**: probability and SHAP contributions

### A question to the assistant

A user asks a tabular scenario's assistant something about the data or the model.

1. **ui-react → Traefik**: AG-UI run, streamed
2. **Traefik → assistant**: routed by hostname
3. **assistant → platform-registry**: entitlement
4. **assistant → llm-gateway**: the model decides which tool to call
5. **llm-gateway → LLM providers**: the active model (rate limit and budget checked)
6. **assistant → prediction**: the tool's call: predict, explain, forecast
7. **llm-gateway → Langfuse**: one trace per model call
8. **assistant → ui-react**: the answer, a chart, the tools it used

### A question answered from documents

A user asks something the scenario's documents answer.

1. **ui-react → rag-agent**: the question, through Traefik
2. **rag-agent → llm-gateway**: embed the question (local-embed)
3. **rag-agent → Qdrant**: the tenant's collection: nearest passages
4. **rag-agent → llm-gateway**: answer from those passages only
5. **rag-agent → ui-react**: the answer with every passage cited

### From raw data to a served model

The pipeline every tabular scenario runs once (make k3s-pipeline).

1. **etl-tabular → SeaweedFS**: raw dataset in, cleaned dataset out
2. **training → SeaweedFS**: reads the cleaned data, writes model, card, out-of-fold scores
3. **training → MLflow**: every candidate and its score
4. **prediction → SeaweedFS**: loads the new model on restart

### A record through the data platform

A sale happens in a shop of a data-product scenario.

1. **Kafka → data-ingest**: the event, on its contract's topic
2. **data-ingest → PostgreSQL**: validated, upserted into bronze (or quarantined)
3. **data-transform → PostgreSQL**: dbt builds silver and gold
4. **data-api → PostgreSQL**: metrics and records, filtered by role
5. **data-api → ui-react**: the live dashboard and the governed doors

### The same question from an external chat (MCP)

A user asks in LibreChat or Open WebUI; the answer needs a tool of the platform.

1. **Open WebUI · LibreChat → Keycloak**: the user signs in
2. **Open WebUI · LibreChat → llm-gateway**: the MCP gateway, with the user's token
3. **llm-gateway → MCP server kit (crm-mcp)**: the tool call, if the user's group may run it
4. **MCP server kit (crm-mcp) → Keycloak**: exchanges the token for one valid only for crm-api
5. **MCP server kit (crm-mcp) → crm-api**: the call, as a pseudonym, over mutual TLS at level 3
6. **MCP server kit (crm-mcp) → OpenTelemetry collector**: an audit line and one trace across the hop
