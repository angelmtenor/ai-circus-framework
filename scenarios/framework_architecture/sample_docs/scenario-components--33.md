# What each scenario is built from - Toxic Management & Work Environment — NLP + ML Tutorial (toxic_leadership, tabular_ml)

Source: generated from every scenario.yaml (this repository)

A handful of people can sour a whole company: managers, salespeople or self-styled "AI experts" — often with an inflated CV — who offload their own job onto the engineers, overload them and spread falsehoods. 4,000 real reviews by software, data and AI engineers, read by a model that combines the words with the facts of the job, learns the vocabulary of toxic management, and races a modern sentence-transformer.

Components it uses:
- Kubernetes (k3s) (infra): Runs every pod this scenario needs.
- LLM providers (infra): The active LLM answers the chat.
- SeaweedFS (data): The dataset and the trained model, under the tenant's prefix.
- PostgreSQL (data): Holds tenants, entitlements and chat history.
- Valkey (data): The gateway's rate-limit and budget counters.
- etl-tabular (data): Cleans the dataset.
- training (data): Trains the model and picks the winner by its metric. Trains the text challenger on sentence embeddings.
- ui-react (consumption): Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.
- prediction (consumption): Serves the model with SHAP explanations for every tab.
- assistant (consumption): The chat: answers from the model's tools, never from memory.
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call.
- llm-gateway (gateway): Every model call of the chat goes through it, with the tenant's limits and budget. Embeds the text for the challenger model (local-embed). The LLM reviewer reads a record against the rubric.
- Langfuse (observability): A trace per LLM call, by tenant and scenario.
- MLflow (observability): Records every training run: candidates, scores, the selected model.

Request flows it follows: A prediction, explained, A question to the assistant, From raw data to a served model.
