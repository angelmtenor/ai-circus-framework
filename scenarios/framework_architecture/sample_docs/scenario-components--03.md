# What each scenario is built from - AML Rulebook Knowledge Graph (aml_regulation_kg, conversational_rag)

Source: generated from every scenario.yaml (this repository)

GraphRAG over the EU Anti-Money Laundering Regulation (EU) 2024/1624: a knowledge graph of obliged entities, obligations, triggers, authorities, records and limits, extracted from 18 articles with a citation and a verbatim quote on every relation. Ask what a bank must do when something looks suspicious and watch the graph light up the path it used.

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
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call. Lists it in the AI-BOM with its declared governance block.
- llm-gateway (gateway): Every model call of the chat goes through it, with the tenant's limits and budget. Embeds questions and passages (local-embed).
- Langfuse (observability): A trace per LLM call, by tenant and scenario.

Request flows it follows: A question answered from documents.
