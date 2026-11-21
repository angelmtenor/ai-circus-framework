# 0019 — MCP doors, starting read-only on a document scenario

Source: docs/adr/0019-mcp-doors-and-tool-policy.md (this repository)

**The chat both ways (added with the first step).**
- **Direct by default.** The chat gets the same reads as local tools (`retrieve_docs`, `list_sources`,
  `read_document`): inside the app, reads stay direct, as ADR 0007 says.
- **"Via MCP" on request.** `mcp.consume` lists scenarios whose doors the chat may use; the user picks
  "🔌 Via MCP" (AG-UI `forwardedProps.route`) and rag-agent loads its tools from those doors with
  `tools/list`, named `mcp__<server>__<tool>`, and the reply is labelled "🔌 MCP".
- **Allow-list by construction.** Only doors of scenarios this rag-agent instance serves, over loopback:
  no URL comes from a scenario file or a user. A door that refuses or fails adds no tools.
- **The caller's token goes along**, so each door checks the caller's entitlement to its own scenario
  again. Token exchange replaces this when doors leave the instance (step 1).
- **Bounded:** `recursion_limit` caps a "via MCP" run's steps; results are capped and passages stay
  tagged as untrusted documents.
- The pilot consumes its own door (`ai_circus_reference`): a demonstration of the mechanism, which
  earns its keep once a chat consumes another product's door (step 2).

**Later steps (each its own decision when reached).**
1. Expose to external clients properly: OAuth 2.1 + PKCE with Keycloak, an audience per MCP server,
   `/.well-known/oauth-protected-resource` (RFC 9728).
2. ~~`data-api` MCP door for `surf_retail` (ADR 0007), proving role row filters and masks.~~ Done:
   `/mcp/{slug}` on data-api for a `data_product` with `mcp.expose` (its data tools: `list_metrics`,
   `query_metric`, `search_records`, `get_record`, `data_health`, `run_sql`). The door settles the
   caller's access with the REST routes' own `access_for` (role, row filter, masks; a platform key
   may send the X-Data-View-As headers) and the tools call the same core functions, so MCP and REST
   cannot drift apart. `run_sql` is listed only for a role that may run SQL.
3. A model as a tool (`prediction`, e.g. `prestaciones_sociales`) through an OpenAPI-wrapping
   server with token exchange.
4. The rest of the chat as an MCP client: the `assistant` service (tabular scenarios), servers beyond
   this instance with an admin-only allow-list, and `allowed_tools` / `max_steps` as policy
   obligations per role. In-app data reads stay direct by default (ADR 0007).

One address in front of every door, llm-gateway's `/mcp/{slug},{slug}` with the caller's token passed
through, is ADR 0020.

**Consequences.** One new dependency in `rag-agent`. A "Connect via MCP" card on a scenario that
declares `mcp:` shows its endpoint and tools (read-only). External clients outside the local
cluster wait for step 1.
