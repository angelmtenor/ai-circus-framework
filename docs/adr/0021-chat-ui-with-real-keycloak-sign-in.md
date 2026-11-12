# 0021 — An optional chat UI with real Keycloak sign-in, tools through the MCP gateway

Builds on ADR 0019 (MCP doors) and ADR 0020 (MCP gateway).

**Context.** The lightweight stack (a chat UI, an AI gateway, an identity provider, MCP servers) needs
a chat UI that signs users in with Keycloak and sends each user's own token to the MCP doors, so the
doors decide by tenant and role. Until now every caller used an API key: the live realm had no users
or organizations, and a token from the realm as imported could not pass `ai_circus_shared.auth`. A
realm imported with its own `clientScopes` gets none of Keycloak's built-in scopes, so tokens had no
`sub`, no realm roles, no email or name, and the organization mapper listed aliases instead of the
`{alias: {id}}` object the services read.

**Decision.**
- **Open WebUI first, LibreChat next.** Open WebUI (official image, pinned, unmodified) is the first
  chat UI: one container, Keycloak sign-in, native MCP. Its licence adds a branding clause to BSD-3
  (no rebranding above 50 users in 30 days without an enterprise licence), so LibreChat (MIT) follows
  as the strictly open-source option. The framework will offer both.
- **Optional bundle**, never part of `k3s-all`: `k8s/chat-ui/`, `make k3s-chat-ui-up` / `-down`,
  compose profile `chat-ui`, at `http://chat.localhost`. Every setting comes from the environment on
  every boot (`ENABLE_PERSISTENT_CONFIG=false`); it runs as UID 1000 with its data on a volume.
- **Sign-in only through Keycloak** (confidential client `open-webui`, PKCE S256; no local passwords).
  The first user to sign in becomes the chat UI's admin; later users get the `user` role.
- **Tools: one MCP connection per door**, to the MCP gateway (`/mcp/{slug}`), with
  `auth_type: system_oauth`: Open WebUI sends the signed-in user's own Keycloak access token,
  refreshing it when needed. One connection per door because a single `Authorization` header goes
  to one door only (ADR 0020). Every user may see both connections; the doors decide.
- **The chat UI's tokens live 15 minutes.** Open WebUI refreshes a token that expires within 5 minutes,
  and the gateway binds an MCP session to the token that opened it: with Keycloak's default 5 minutes,
  every request carried a new token and the gateway refused the session (403, owner mismatch).
- **Models and embeddings through llm-gateway**; no provider key and no local model in the chat UI.
- **Keycloak serves two URL sets**: `KC_HOSTNAME=http://keycloak.localhost` with
  `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`, so a server-side client in the cluster gets in-cluster
  token and JWKS URLs while the issuer and login page stay the public ones.
- **`realm-export.json` stays the single source of truth** and now declares the scopes a token needs:
  `basic` (`sub`), `profile`, `email`, `roles` (`realm_access.roles`), `data-access` (row-filter claims
  such as surf_retail's `stores`, from an admin-only user attribute), the organization mapper with the
  id, surf_retail's realm roles and the `open-webui` client (no secret in the file). `ui-react` gets
  the same scopes. `--import-realm` applies the file only on a realm's first boot, so
  `scripts/chat_ui_identity.py` (run by `make k3s-chat-ui-up`) brings a live realm up to it: missing
  scopes, mappers, roles, clients and default scopes, the client secret from `.env`, and a demo tenant
  (`lite-demo`: an analyst and surf_retail's S012 store manager) with its entitlements. Secrets are
  generated into `.env` and travel over stdin, never as arguments.
- **Token exchange moves to the next step.** In the framework the doors are themselves the APIs; a
  per-API token (T2) needs an MCP server in front of a separate API, which the MCP server kit brings.

**Verified on k3s** with real sign-ins in a headless browser: both demo users sign in through
Keycloak; Open WebUI lists 6 surf_retail tools for the analyst (with `run_sql`) and 5 for the store
manager, each through the gateway with that user's token; in the chat, the store manager's question
calls `data_health` and the answer is "My role is Store manager, and I can only see data for store
S012"; the analyst's says Retail Analyst with every store. The document door answers "nothing indexed
for your organisation yet" for the new tenant, as tenant isolation requires.

**Consequences.** Real Keycloak users work end to end for the first time, in every service, since the
realm fixes apply to `ui-react` too. One optional pod (about 660 MiB idle, measured). Open WebUI drops a `?tools=`
link parameter if its tool list has not loaded yet, so a user switches a door on from the chat's
Integrations menu. Per-group visibility of doors in the chat UI and LibreChat are later steps.
