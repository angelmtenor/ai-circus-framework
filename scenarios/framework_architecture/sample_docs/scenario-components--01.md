# What each scenario is built from - AI Open Framework Reference Guide (ai_circus_reference, conversational_rag)

Source: generated from every scenario.yaml (this repository)

Agent-based RAG chatbot answering questions grounded in the ai-circus project's own reference notes: dev environment/tooling, software engineering practices, ML/DS workflow, and generative AI / agent / RAG best practices.

Components it uses:
- Kubernetes (k3s) (infra): Runs every pod this scenario needs.
- LLM providers (infra): The active LLM answers the chat.
- SeaweedFS (data): The source documents.
- PostgreSQL (data): Holds tenants, entitlements and chat history.
- Valkey (data): The gateway's rate-limit and budget counters.
- Qdrant (data): The tenant's collection of passages.
- etl-vectorize (data): Chunks and embeds the documents into the tenant's collection.
- ui-react (consumption): Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.
- rag-agent (consumption): Answers from the retrieved passages and cites each one.
- Open WebUI · LibreChat (consumption): Optional: the same tools from LibreChat or Open WebUI, with the user's own token.
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call.
- llm-gateway (gateway): Every model call of the chat goes through it, with the tenant's limits and budget. Embeds questions and passages (local-embed). The MCP gateway routes its read-only door to MCP clients.
- Langfuse (observability): A trace per LLM call, by tenant and scenario.

Request flows it follows: A question answered from documents.
