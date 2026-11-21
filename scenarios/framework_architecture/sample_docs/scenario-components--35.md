# What each scenario is built from - Reservoirs & Drought Watch (water_reservoirs_drought, tabular_ml)

Source: generated from every scenario.yaml (this repository)

Where is the water stored, and where is it heading? A daily storage forecast for nine reservoirs and four supply systems (p10/p50/p90 at 7, 30, 90 and 180 days) on a map of the basin, the state against an illustrative drought plan, what-if and measure comparison, data-quality flags and a weekly bulletin — for a fictional water authority, on synthetic data. Illustrative decision support: not an operational tool.

Components it uses:
- Kubernetes (k3s) (infra): Runs every pod this scenario needs.
- LLM providers (infra): The active LLM answers the chat.
- SeaweedFS (data): The dataset and the trained model, under the tenant's prefix.
- PostgreSQL (data): Holds tenants, entitlements and chat history.
- Valkey (data): The gateway's rate-limit and budget counters.
- etl-tabular (data): Cleans the dataset.
- training (data): Trains the model and picks the winner by its metric.
- ui-react (consumption): Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.
- prediction (consumption): Serves the model with SHAP explanations for every tab.
- assistant (consumption): The chat: answers from the model's tools, never from memory. Adds the drought watch tools and the routes of its control room.
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call. Lists it in the AI-BOM with its declared governance block.
- llm-gateway (gateway): Every model call of the chat goes through it, with the tenant's limits and budget.
- Langfuse (observability): A trace per LLM call, by tenant and scenario.
- MLflow (observability): Records every training run: candidates, scores, the selected model.

Request flows it follows: A prediction, explained, A question to the assistant, From raw data to a served model.
