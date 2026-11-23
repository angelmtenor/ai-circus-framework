# Architecture details - The MCP door (docs/adr/0019)

Source: docs/architecture-details.md (this repository)

A `tabular_ml` scenario opens its door on the **assistant** (docs/adr/0031): `mcp.expose` from `MCP_MODEL_TOOLS`
(`describe_model`, `score_record`: a classifier or a regressor only) and, with `analysis_toolset: drought_watch`,
`MCP_DROUGHT_TOOLS`. The assistant's `core/mcp_door.py` has the same `McpDoor` shape and forwards the caller's own bearer
token to prediction (`/model/{slug}/card`, `/predict/{slug}`), which checks it again. `score_record` fills the inputs the
caller left out from `feature_schema` defaults, names what the number means, returns the eight largest SHAP drivers and,
with business rules, the gate, the decision and the rules that fired. The drought tools are the chat's `DroughtToolkit`
reads, over the forecast prediction serves the caller's organisation (asked with the same token) when it was trained
from the same readings, and the committed one otherwise. `model_views` drops the door (it is the scenario's, and scores the main model). TabularView's "Connect via MCP"
tab (analyst and engineer views) reuses `McpConnectView` with the assistant's URL and `localhost:8034`. Before a tabular
YAML declares `mcp:`, rebuild every image that validates tabular files (registry, assistant, prediction, etl-tabular,
training, etl-vectorize, data-transform, dl-training): an older image rejects the block.

**One address for every door** (docs/adr/0020): llm-gateway's LiteLLM is also the MCP gateway. `litellm_config.yaml`'s
`mcp_servers` lists each door under its scenario slug, all `auth_type: true_passthrough`, so `/mcp/{slug},{slug}` reaches
several doors with the caller's own token, the tools come back prefixed (`surf_retail-query_metric`), and each door still
decides tenant, role and tools; `extra_headers` forwards data-api's View-as pair. With several doors in the URL, LiteLLM
withholds a request-wide `Authorization` (one token is never replayed to several upstreams) and the client sends one
`x-mcp-{slug}-authorization` per door; a door that refuses just contributes no tools. `/mcp` without a server falls back
to LiteLLM's own key check. `tests/test_mcp_gateway.py` pins the contract (passthrough only, no credential, in-cluster
doors of scenarios with `mcp.expose`, LiteLLM's own gating and no fan-out). The gateway has no Traefik router; `make
k3s-mcp-portforward` adds `localhost:8033`.

**An optional chat UI with real sign-in** (docs/adr/0021): `make k3s-chat-ui-up` serves Open WebUI at `chat.localhost`
(`k8s/chat-ui/`, compose profile `chat-ui`), Keycloak-only sign-in through the confidential `open-webui` client, models
and embeddings through llm-gateway, and one MCP connection per door to the gateway with `auth_type: system_oauth`, so
each door sees the signed-in user's own token and applies its tenant and roles (an analyst gets `run_sql`, the S012 store
manager does not and sees only S012). `scripts/chat_ui_identity.py` brings the live realm up to `realm-export.json`
(which now issues `sub`, realm roles, the `{alias: {id}}` organization claim and row-filter claims such as `stores`) and
creates the `lite-demo` tenant. The client's tokens live 15 minutes because Open WebUI refreshes within 5 and the gateway
binds an MCP session to its token.
