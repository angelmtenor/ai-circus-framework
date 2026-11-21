# What each scenario is built from - Ayudas de Emergencia Social — Valoración inteligente (prestaciones_sociales, tabular_ml)

Source: generated from every scenario.yaml (this repository)

Que el dinero público llegue a quien de verdad lo necesita: ni fraude, ni ayudas concedidas a mano a personas conocidas, ni ayudas para lo que el solicitante puede pagarse. Las mismas reglas de negocio para todas las solicitudes, un modelo que lee a la vez las casillas y el texto libre, y una propuesta explicada casilla a casilla — conceder, denegar o revisión manual. Siempre decide una persona.

Components it uses:
- Kubernetes (k3s) (infra): Runs every pod this scenario needs.
- LLM providers (infra): The active LLM answers the chat.
- SeaweedFS (data): The dataset and the trained model, under the tenant's prefix.
- PostgreSQL (data): Holds tenants, entitlements and chat history.
- Valkey (data): The gateway's rate-limit and budget counters.
- Qdrant (data): The tenant's collection of passages.
- etl-tabular (data): Cleans the dataset and applies its business rules.
- training (data): Trains the model and picks the winner by its metric. Trains the text challenger on sentence embeddings.
- etl-vectorize (data): Chunks and embeds the documents into the tenant's collection.
- ui-react (consumption): Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.
- prediction (consumption): Serves the model with SHAP explanations for every tab.
- assistant (consumption): The chat: answers from the model's tools, never from memory.
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call. Lists it in the AI-BOM with its declared governance block.
- llm-gateway (gateway): Every model call of the chat goes through it, with the tenant's limits and budget. Embeds the text for the challenger model (local-embed). Embeds questions and passages (local-embed).
- Langfuse (observability): A trace per LLM call, by tenant and scenario.
- MLflow (observability): Records every training run: candidates, scores, the selected model.

Request flows it follows: A prediction, explained, A question to the assistant, From raw data to a served model.
