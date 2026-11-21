# 0024 — One catalog for the MCP path

Source: docs/adr/0024-one-mcp-catalog.md (this repository)

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
