# Architecture details - The MCP door (docs/adr/0019)

Source: docs/architecture-details.md (this repository)

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

**The MCP lite profile** (docs/adr/0027): `make k3s-mcp-lite` runs the MCP Lite Stack alone on its own k3d cluster
(`mcp-lite`, own kubeconfig file, port 80, so the framework cluster is paused meanwhile): `k8s/mcp-lite/` lists base's
Postgres, Keycloak, Valkey and llm-gateway (Langfuse off) plus `k8s/mcp-examples`, and `k8s/mcp-lite/open-webui|librechat`
add one chat UI with the kit servers only (generated from the catalog; scenario doors need the full framework). Built
with `kubectl kustomize --load-restrictor LoadRestrictionsNone`, since it lists the framework's own files. `make
k3s-mcp-lite-verify` (`scripts/mcp_lite_check.py`) signs the demo users in through Keycloak's login pages with the chat
UI's client and checks each kit server's tools, a granted call and a refused one, against the catalog, without a model.
`OBS=1` adds the observability bundle on the lite cluster (never run `make k3s-obs-up` bare while it is up: it acts on
the current context); the check then also finds the calls' metrics, audit lines and traces, and no token in any log.
LibreChat traces through its own OpenTelemetry switch (`OBS_LIBRECHAT_ENV`), wired by `k3s-obs-up` in both profiles.
