# What each scenario is built from - Sede Electrónica — Official Tax Forms (sede_electronica, assisted_form)

Source: generated from every scenario.yaml (this repository)

A Spanish-style electronic office: describe your procedure or upload your documents, and the assistant picks the right official model, reads your ID and certificates, fills every numbered box, checks DNI and IBAN, and files it with a PDF receipt.

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
