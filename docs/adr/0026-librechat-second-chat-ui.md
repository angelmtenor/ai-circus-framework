# 0026 — LibreChat as the second chat UI

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

**Decision.**
- **Same shape as Open WebUI, one bundle per UI.** `k8s/chat-ui-librechat/` (LibreChat + its own MongoDB, never in
  `k3s-all`), `make k3s-librechat-up` / `-down`, compose profile `chat-ui-librechat`, at `http://librechat.localhost`.
  Official images, pinned and unmodified (`ghcr.io/librechat-ai/librechat:v0.8.8`, UID 1000; `mongo:8.0.20`, UID 999,
  authentication on, a 0.25 GB cache). On a 16 GB laptop one chat UI runs at a time; the up target says so.
- **Sign-in only through Keycloak**: confidential client `librechat` (PKCE S256, 15-minute tokens, the same default
  scopes as `open-webui`, so `mcp-servers` puts the kit servers in the token's audience), no local accounts,
  automatic redirect. `scripts/chat_ui_identity.py` creates the client and generates LibreChat's keys into `.env`
  (credential key 32 bytes, IV 16, JWT and session secrets, the MongoDB password), filling an empty `KEY=` line in
  place because a repeated key makes `kubectl create secret --from-env-file` refuse the Secret. Same demo tenant.
- **The https rule is relaxed in the lab only, by a load hook, not a fork.** `oidc-allow-http.mjs`, loaded with
  `NODE_OPTIONS=--import`, adds openid-client's own `allowInsecureRequests` to every discovery. If an upgrade changes
  the function it patches, the server stops with an error naming the file. With an https identity provider, the file
  and that line go. Making Keycloak https for the whole lab would have changed every service, the browser's trust store
  and both laptops for one optional UI.
- **The catalog generates LibreChat's MCP block** (`librechat-config.yaml`, between the usual `BEGIN`/`END` markers,
  mounted by k8s and compose alike): one `streamable-http` server per catalog server at `<gateway>/mcp/<name>`, each
  with `Authorization: Bearer {{LIBRECHAT_OPENID_ACCESS_TOKEN}}` (one door per header, ADR 0020), `requiresOAuth:
  false`, no `obo` and no shared key; `mcpSettings.allowedAddresses` holds the gateway alone. A title is shown with
  the characters LibreChat refuses turned into spaces ("Customers (CRM)" → "Customers CRM"), and the catalog now
  refuses `{{` in any text, as it refuses `$`.
- **Living with the session binding.** The tokens live 15 minutes, as Open WebUI's, and idle MCP connections close
  after 5 minutes (`MCP_USER_CONNECTION_IDLE_TIMEOUT`, default 15), so a user coming back after a pause starts on a
  fresh session. During continuous use one call per token change is refused and retried by the model; changing that
  needs LibreChat to reopen its session when it refreshes the bearer, or LiteLLM to bind a session to the subject.
- **Only what the framework provides.** Models from llm-gateway's `/v1` (a custom endpoint, `gemini-flash` by default,
  every gateway model selectable); no web search, code runner, file search, memories, Meilisearch or RAG API; users
  cannot create or share MCP servers or agents. JSON logs (`CONSOLE_JSON`). LibreChat's own log lines name a user's
  email at sign-in; they are a third-party product's logs, as Open WebUI's are.

**Verified on k3s** with real sign-ins in a headless browser (Playwright, Chromium). The store manager's question
called `surf_retail-data_health` through the gateway and the answer was "You have the role of Store manager and you
are currently authorized to view data for store S012"; the analyst's said Analyst with all 25 stores. On the Level 3
`crm` server the analyst ran `top_customers`, the manager had only `search_customers` and `get_customer`; crm-mcp's
audit lines show each call under that user's own subject. With the client's token life cut to 2 minutes, the call
after the refresh was refused (403 owner mismatch), the connection reopened and the model's retry succeeded. Idle
after real use: LibreChat 435 MiB, MongoDB 207 MiB.

**Consequences.** The framework offers two chat UIs over one MCP path: the catalog decides both UIs' servers, the
gateway and the servers decide access, and either UI can be left out. A second optional database (MongoDB, SSPL,
used unmodified as LibreChat's store). One refused-then-retried call per token change in LibreChat, observable as
`owner mismatch` warnings in the gateway's log. Per-group visibility of servers in LibreChat is not generated:
every user sees every server, and the door or the server's policy decides.
