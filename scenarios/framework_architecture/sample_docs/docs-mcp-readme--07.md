# MCP Lite Stack - Known limits

Source: docs/mcp/README.md (this repository)

- **LibreChat needs an https identity provider.** The lab's Keycloak is http, so a small lab-only load hook turns on
  the OpenID library's own opt-out (`k8s/chat-ui-librechat/oidc-allow-http.mjs`). Delete it with an https provider.
- **LibreChat refuses one call after a token refresh.** The gateway binds an MCP session to the token that opened it;
  LibreChat reopens the connection and the model retries. Idle connections close after 5 minutes to keep it rare.
- **Open WebUI sends no trace context** with its MCP calls, so its hop into a server is matched through the audit
  lines; LibreChat's is one trace.
- **Every user sees every server** in the chat UIs; the server refuses what the role is not granted. Per-group
  visibility would tie the framework to each chat UI's internal database.
- **Chat UIs keep the real user id.** Pseudonyms there would need every chat account moved to the new id.
