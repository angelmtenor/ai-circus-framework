# What each scenario is built from - Payment Fraud & Financial Crime (financial_crime_desk, tabular_ml)

Source: generated from every scenario.yaml (this repository)

A bank's financial-crime desk: which cross-border transfers to escalate as possible money laundering, terrorist financing or sanctions evasion, read field by field and word by word by a model, a transformer challenger and an LLM analyst; which customer payments are fraud, with rules before the model; and which accounts are money mules, on the network of who pays whom. Every alert explained. Synthetic data.

Components it uses:
- Kubernetes (k3s) (infra): Runs every pod this scenario needs.
- LLM providers (infra): The active LLM answers the chat.
- SeaweedFS (data): The dataset and the trained model, under the tenant's prefix.
- PostgreSQL (data): Holds tenants, entitlements and chat history.
- Valkey (data): The gateway's rate-limit and budget counters.
- Qdrant (data): The tenant's collection of passages.
- etl-tabular (data): Cleans the dataset.
- training (data): Trains each of its 3 models and picks the winner by its metric. Trains the text challenger on sentence embeddings.
- etl-vectorize (data): Chunks and embeds the documents into the tenant's collection.
- ui-react (consumption): Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.
- prediction (consumption): Serves the 3 models with SHAP explanations for every tab.
- assistant (consumption): The chat: answers from the model's tools, never from memory. Serves the model's read-only MCP door, with the caller's own token.
- Open WebUI · LibreChat (consumption): Optional: the same tools from LibreChat or Open WebUI, with the user's own token.
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call. Lists it in the AI-BOM with its declared governance block.
- llm-gateway (gateway): Every model call of the chat goes through it, with the tenant's limits and budget. Embeds the text for the challenger model (local-embed). The LLM reviewer reads a record against the rubric. Embeds questions and passages (local-embed). The MCP gateway routes its read-only door to MCP clients.
- Langfuse (observability): A trace per LLM call, by tenant and scenario.
- MLflow (observability): Records every training run: candidates, scores, the selected model.

Request flows it follows: A prediction, explained, A question to the assistant, From raw data to a served model.
