# What each scenario is built from - Public Service Request Portal (service_request, assisted_form)

Source: generated from every scenario.yaml (this repository)

Report an issue or request a service from a fictional local government — a broken streetlight, a missed trash pickup, a permit application — and let the assistant fill out the form for you as you describe it. Grounded in a small catalog of request types (RAG), which also drives which fields are actually required.

Components it uses:
- Kubernetes (k3s) (infra): Runs every pod this scenario needs.
- LLM providers (infra): The active LLM answers the chat.
- SeaweedFS (data): Each submission, by tenant and user.
- PostgreSQL (data): Holds tenants, entitlements and chat history.
- Valkey (data): The gateway's rate-limit and budget counters.
- Qdrant (data): The tenant's collection of passages.
- etl-vectorize (data): Chunks and embeds the documents into the tenant's collection.
- ui-react (consumption): Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.
- form-agent (consumption): Fills the form through conversation, validates it and prints the PDF.
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call.
- llm-gateway (gateway): Every model call of the chat goes through it, with the tenant's limits and budget. Embeds questions and passages (local-embed).
- Langfuse (observability): A trace per LLM call, by tenant and scenario.
