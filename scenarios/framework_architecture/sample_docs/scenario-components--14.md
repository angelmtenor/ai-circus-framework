# What each scenario is built from - Insurance Retention & Next Best Offer (insurance_portfolio, tabular_ml)

Source: generated from every scenario.yaml (this repository)

A multi-line insurer's portfolio (motor, home, health, life, pet, funeral, accident): who is about to lapse at renewal and why, who is ready to add a product, and which product goes with what each customer already holds. Three models, SHAP explanations and a chat assistant over all of them. Synthetic data.

Components it uses:
- Kubernetes (k3s) (infra): Runs every pod this scenario needs.
- LLM providers (infra): The active LLM answers the chat.
- SeaweedFS (data): The dataset and the trained model, under the tenant's prefix.
- PostgreSQL (data): Holds tenants, entitlements and chat history.
- Valkey (data): The gateway's rate-limit and budget counters.
- etl-tabular (data): Cleans the dataset.
- training (data): Trains each of its 3 models and picks the winner by its metric.
- ui-react (consumption): Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.
- prediction (consumption): Serves the 3 models with SHAP explanations for every tab.
- assistant (consumption): The chat: answers from the model's tools, never from memory. Serves the model's read-only MCP door, with the caller's own token.
- Open WebUI · LibreChat (consumption): Optional: the same tools from LibreChat or Open WebUI, with the user's own token.
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call.
- llm-gateway (gateway): Every model call of the chat goes through it, with the tenant's limits and budget. The MCP gateway routes its read-only door to MCP clients.
- Langfuse (observability): A trace per LLM call, by tenant and scenario.
- MLflow (observability): Records every training run: candidates, scores, the selected model.

Request flows it follows: A prediction, explained, A question to the assistant, From raw data to a served model.
