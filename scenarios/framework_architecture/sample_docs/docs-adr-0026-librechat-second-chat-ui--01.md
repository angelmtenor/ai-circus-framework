# 0026 — LibreChat as the second chat UI

Source: docs/adr/0026-librechat-second-chat-ui.md (this repository)

Builds on ADR 0021 (an optional chat UI with real Keycloak sign-in), ADR 0020 (the MCP gateway) and ADR 0024
(one MCP catalog).

**Context.** ADR 0021 shipped Open WebUI and promised LibreChat as the strictly open-source option: Open WebUI's
licence adds a branding clause to BSD-3, LibreChat is MIT. LibreChat 0.8.8 (1 Oct 2026) signs users in with any
OpenID provider, keeps the provider's own tokens when `OPENID_REUSE_TOKENS=true`, and puts the signed-in user's
access token into an MCP server's headers through the `{{LIBRECHAT_OPENID_ACCESS_TOKEN}}` placeholder. It keeps its
state in MongoDB. Reading its code (the image ships its sources) and running it against the framework found four
things the documentation does not say:

- **Its OpenID client accepts only https providers.** openid-client 6 refuses `http://` for discovery, token, JWKS
  and authorization endpoints unless the caller passes `allowInsecureRequests`, and LibreChat never does (upstream
  issue 8305 was closed without a setting). The lab's Keycloak is http (`http://keycloak.localhost`).
- **It refuses private addresses** (SSRF protection): every Kubernetes Service resolves to one, so the gateway is
  unreachable until `mcpSettings.allowedAddresses` exempts it by host and port.
- **An MCP server's title** may hold only letters, digits, marks, spaces, hyphens and apostrophes.
- **It keeps a user's MCP connection open across a token refresh** and swaps the new bearer into it, while the
  gateway binds an MCP session to the token that opened it (ADR 0021). The first call after any token change (a
  refresh, or signing in again while a connection is open) is refused with 403 "owner mismatch"; LibreChat reopens
  the connection and returns "retry the tool deliberately" to the model. It never replays the call itself, since the
  server may have run it. LiteLLM's owner binding hashes the bearer and has no setting.
