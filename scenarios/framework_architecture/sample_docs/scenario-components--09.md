# What each scenario is built from - Building Energy Consumption (energy_building, tabular_ml)

Source: generated from every scenario.yaml (this repository)

Two models of a home's appliance energy: one predicts the use (in Wh) under given indoor climate, outdoor weather and time of day, the other forecasts the next 24 hours hour by hour, with prediction bands, a backtest on past days and what-ifs on the weather.

Components it uses:
- Kubernetes (k3s) (infra): Runs every pod this scenario needs.
- LLM providers (infra): The active LLM answers the chat.
- SeaweedFS (data): The dataset and the trained model, under the tenant's prefix.
- PostgreSQL (data): Holds tenants, entitlements and chat history.
- Valkey (data): The gateway's rate-limit and budget counters.
- etl-tabular (data): Cleans the dataset.
- training (data): Trains each of its 2 models and picks the winner by its metric.
- ui-react (consumption): Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.
- prediction (consumption): Serves the 2 models with SHAP explanations for every tab.
- assistant (consumption): The chat: answers from the model's tools, never from memory.
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call.
- llm-gateway (gateway): Every model call of the chat goes through it, with the tenant's limits and budget.
- Langfuse (observability): A trace per LLM call, by tenant and scenario.
- MLflow (observability): Records every training run: candidates, scores, the selected model.

Request flows it follows: A prediction, explained, A question to the assistant, From raw data to a served model.
