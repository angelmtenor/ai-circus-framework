# 0021 — An optional chat UI with real Keycloak sign-in, tools through the MCP gateway

Source: docs/adr/0021-chat-ui-with-real-keycloak-sign-in.md (this repository)

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
