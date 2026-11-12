# 0024 — One catalog for the MCP path

Builds on ADR 0020 (MCP gateway), ADR 0021 (chat UI with Keycloak sign-in) and ADR 0022 (the MCP server kit).

**Context.** One MCP server touched five hand-edited places: llm-gateway's `mcp_servers`, the chat UI's
`TOOL_SERVER_CONNECTIONS` (twice: k8s and compose), the server's `policy.yaml`, and three Keycloak entries (the
server's client, its API's client and audience scope) plus a mapper in the `mcp-servers` scope. Nothing kept them
consistent: a server could be routed but have no Keycloak client, or a tool could be granted that did not exist.
The access rules would end up in two places, the gateway's visibility and the server's policy, and drift.

**Decision.**
- **`mcp/catalog.yaml` is the single source of truth.** It lists the gateway's address, every server and every
  group. A server is one of two kinds:
  - `scenario_door`: a scenario's own door (ADR 0019), with its address, title and description and optional
    forwarded headers. Its tools follow the tenant's entitlement and the scenario's own roles, so **no group may
    list it**: the catalog routes it and never grants on it, and the rules stay in one place (`scenario.yaml`).
  - `kit_server`: an MCP server in front of an API, built with `libs/mcp-kit` (ADR 0022): its service, its API,
    title, description, optional tenants and its tools.

  A group is a realm role in the user's token; it maps kit servers to tools (or `"*"`).
- **A generator, `scripts/generate_mcp_catalog.py`, writes everything the catalog decides**: the gateway's
  `mcp_servers`, the chat UI's connections (k8s and compose), each kit server's `policy.yaml`, and the Keycloak
  entries in `infra/keycloak/realm-export.json` (the server's confidential exchange client, its API's client and
  `<api>-audience` scope, one audience mapper per server in `mcp-servers`). Hand-written files keep their own
  comments: a generated block sits between `BEGIN`/`END` marker comments, and the Keycloak entries it owns say
  "Generated from mcp/catalog.yaml." (so removing a server removes them too). The catalog itself has one
  canonical layout that the generator also writes, so a hand edit and the portal produce the same file.
- **The generator refuses what would route, grant or exchange by mistake**: a server name with `-` (LiteLLM's
  tool prefix separator), a door that is not a scenario's declared door or not at `/mcp/<name>`, a forwarded
  credential header, a kit server whose service does not use the kit, a tool list different from the
  `@kit.tool()` functions in the server's code, a grant on a scenario door or of an unknown tool, a group that is
  not a realm role, a client that would replace a hand-written one (the sign-in clients), and `$` in a text
  (docker compose and kubelet would expand it).
- **Drift fails CI.** `libs/shared/tests/test_mcp_catalog.py` (the shared-lib job) regenerates in memory and
  compares; `make mcp-catalog-check` runs the same check, `make mcp-catalog` writes. Each kit server's own tests
  pin the catalog's tool list against the tools it really registers.
- **Secrets by convention.** A kit server's client secret is `<CLIENT_ID>_CLIENT_SECRET` in `.env`
  (`crm-mcp` → `CRM_MCP_CLIENT_SECRET`); `scripts/chat_ui_identity.py` finds every exchanging client in the
  realm export, generates a missing secret and syncs it, so a new server needs no edit there.
- **Putting it on a cluster** is `make k3s-mcp-catalog-apply`: Keycloak sync, then rebuild, import and restart
  llm-gateway and every deployed kit server (their config is baked into the image), then the chat UI's
  connections if it is deployed.
- **An optional local portal** (`make mcp-catalog-portal`, `scripts/mcp_catalog_portal.py` + `mcp/portal.html`)
  for people who should not edit YAML: a grid of groups × tools per kit server, add or remove a server (it
  offers only kit services and scenario doors that exist), review every generated change as a diff, publish.
  Publishing runs the generator's own validation and writes the same files as `make mcp-catalog`; it never
  deploys or commits. It binds `127.0.0.1`, answers only to its own `Host`, requires a per-run token that only
  its page carries (a page of another origin can read neither the token nor the API), sends a nonce-only script
  policy, and refuses to overwrite a catalog edited on disk since the page loaded it.

**What the catalog does not decide.** Chat UI *visibility* per group: Open WebUI grants a connection to a group by
its internal id, which exists only after that group's first sign-in, so every signed-in user sees every connection.
Visibility is not the control: the door or the server's policy refuses a caller on every call. One gateway URL per
group (`/mcp/a,b`) does not help Open WebUI either, since it sends one `Authorization` and LiteLLM never fans one
token out to several servers (ADR 0020). Clients that bind one credential per server can use such a URL.
Deploying a server (its manifest, compose entry, secret spec, image) stays the new-service checklist.

**Consequences.** Adding an API is one catalog entry plus one scaffolded server with its tools; the routes, chat
connection, policy and Keycloak clients follow, and CI fails if any of them drifts. Removing a server removes its
route, connection and Keycloak entries. A standalone stack takes the catalog, the generator and the portal as is,
pointing the generator at its own files.
