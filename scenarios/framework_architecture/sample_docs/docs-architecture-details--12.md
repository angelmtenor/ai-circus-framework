# Architecture details - The MCP door (docs/adr/0019)

Source: docs/architecture-details.md (this repository)

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

**A second chat UI, LibreChat** (docs/adr/0026, MIT): `make k3s-librechat-up` serves it at `librechat.localhost`
(`k8s/chat-ui-librechat/` with its own MongoDB, compose profile `chat-ui-librechat`; one chat UI at a time on 16 GB).
Keycloak-only sign-in through the confidential `librechat` client with `OPENID_REUSE_TOKENS`, so each MCP server's
`Authorization: Bearer {{LIBRECHAT_OPENID_ACCESS_TOKEN}}` is the user's own Keycloak token; models from llm-gateway
(`gemini-flash` by default). Its `librechat-config.yaml` holds the catalog-generated `mcpServers` and the gateway as the
only exemption from its private-address block (`mcpSettings.allowedAddresses`). Lab only: LibreChat's OpenID client
accepts only https providers, so `oidc-allow-http.mjs` (a Node load hook, `NODE_OPTIONS=--import`) turns on the library's
`allowInsecureRequests`; delete it with an https identity provider. LibreChat keeps an MCP connection across a token
refresh, so the first call after one gets the gateway's 403 owner mismatch, the connection reopens and the model retries;
idle connections close after 5 minutes to keep that rare.
