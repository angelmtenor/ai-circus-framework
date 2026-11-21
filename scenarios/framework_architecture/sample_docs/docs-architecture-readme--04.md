# Architecture guide - One request, end to end

Source: docs/architecture/README.md (this repository)

A prediction in a model tab:

1. `ui-react` sends `POST /predict/{scenario}` with the user's token to `prediction.localhost`.
2. Traefik routes it to `prediction` by hostname.
3. `prediction` checks the token (signature, organization) and asks `platform-registry` whether this tenant may use
   this scenario. The answer is cached for 30 seconds; the UI's own filtering is never trusted.
4. The tenant's model was loaded from SeaweedFS at start-up (or the shared fallback model when the tenant has none),
   so the first prediction is instant.
5. The answer is the probability and the SHAP contribution of every input.

A question to the assistant follows the same first three steps, then the LLM, through `llm-gateway`, decides which
tool to call (`predict`, `explain`, a forecast, a data query); the assistant runs it and the LLM answers from the
result. `llm-gateway` checks the tenant's rate limit and budget, and sends a trace to Langfuse. Six such flows are
spelled out step by step in [components.md](components.md#request-flows), and the app animates them on its
Architecture page and on each scenario's Overview.
