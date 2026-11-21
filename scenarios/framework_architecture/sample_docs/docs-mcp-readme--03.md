# MCP Lite Stack - The pieces

Source: docs/mcp/README.md (this repository)

| Piece | What it does | Where | ADR |
|---|---|---|---|
| Scenario doors | A scenario's own read-only MCP door: documents (`rag-agent`) or governed data (`data-api`), under the existing entitlements | `mcp:` in `scenario.yaml` | [0019](../adr/0019-mcp-doors-and-tool-policy.md) |
| AI gateway | `llm-gateway` is also the MCP gateway: `/mcp/<server>`, every route `true_passthrough` (the caller's token goes through, the server decides); forwards the caller's trace context, never baggage | `services/llm-gateway/litellm_config.yaml` (generated block) | [0020](../adr/0020-mcp-gateway-without-enterprise.md), [0028](../adr/0028-masked-logs-and-one-trace-through-the-mcp-hop.md) |
| Chat UIs | Open WebUI (`chat.localhost`) and LibreChat (`librechat.localhost`, MIT): Keycloak-only sign-in, each user's token to every server | `k8s/chat-ui/`, `k8s/chat-ui-librechat/` | [0021](../adr/0021-chat-ui-with-real-keycloak-sign-in.md), [0026](../adr/0026-librechat-second-chat-ui.md) |
| MCP server kit | `libs/mcp-kit`: token validation, the access policy, the per-API exchange, audit lines; a server's own code is its tools only. Example: `crm-mcp` in front of `crm-api` | `libs/mcp-kit/`, `services/crm-mcp/`, `services/crm-api/` | [0022](../adr/0022-mcp-server-kit-and-per-api-tokens.md) |
| Observability | `mcp_kit_*` metrics, audit lines with trace ids, one trace from the chat UI to the API; the collector masks tokens and emails in every stored log line | `observability/` (`make k3s-obs-up`) | [0023](../adr/0023-observability-of-the-mcp-path.md), [0028](../adr/0028-masked-logs-and-one-trace-through-the-mcp-hop.md) |
| Catalog | One file for servers and grants; a generator writes the gateway routes, both chat UIs' connections, each kit server's `policy.yaml` and the Keycloak clients; CI fails on drift; an optional local portal edits it | `mcp/catalog.yaml`, `scripts/generate_mcp_catalog.py` | [0024](../adr/0024-one-mcp-catalog.md) |
| Level 3 | For regulated servers: DPoP-bound per-API tokens, live token checks, mutual TLS to the API with one CA per pair | `level: 3` in the catalog | [0025](../adr/0025-level-3-regulated-mcp-servers.md) |
| Lite profile | The stack alone on its own k3d cluster, from the framework's own manifests, with a check that needs no model | `k8s/mcp-lite/`, `scripts/k3s_mcp_lite.sh` | [0027](../adr/0027-mcp-lite-profile.md) |
| Pseudonyms | Each API sees Keycloak's pairwise pseudonym of the user; an identity admin can map it back for an audit | generated Keycloak mapper, `scripts/mcp_pseudonym.py` | [0029](../adr/0029-pseudonymous-users-at-the-apis.md) |

### Security levels

| Level | Token at the server | Token at the API | Use |
|---|---|---|---|
| Door | the user's own (the door is the API) | — | a scenario's documents or governed data |
| 2 (default kit server) | the user's own, validated by signature | exchanged for this API only, minutes long | an MCP server in front of an existing API |
| 3 (regulated) | the user's own, also checked live at Keycloak | exchanged, DPoP-bound, checked live; mutual TLS | banking, insurance, health |

### Who sees which identity

| Where | Sees the user as |
|---|---|
| Chat UI, AI gateway, MCP servers' audit lines | the Keycloak id (chat accounts are bound to it; the audit trail is the platform's own record) |
| Each API behind a kit server | its own stable pseudonym; two APIs cannot join their logs |
| Stored logs | never an email address or a token: the collector masks them, third-party lines included |
