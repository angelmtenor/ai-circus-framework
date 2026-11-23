# Architecture details - The MCP door (docs/adr/0019)

Source: docs/architecture-details.md (this repository)

The whole MCP path (chat UIs, gateway, kit servers, catalog, Level 3, observability, pseudonyms, the lite profile) as a
guide: [docs/mcp](mcp/README.md). The paragraphs below are the implementation notes.

A `conversational_rag` scenario may open a **read-only MCP door** on its documents (`mcp: {expose: [list_sources,
search_documents, read_document]}`, needs `vector_store`; first on `ai_circus_reference`). rag-agent mounts one stateless
FastMCP app (Streamable HTTP, JSON responses) at `/mcp`; `McpDoor` (`core/mcp_door.py`) takes `/mcp/{slug}`, answers 404
for a scenario without a door and 401/403 from `resolve_caller_identity` *before* any MCP handling, then puts the caller
in the request scope's state: the tools read the tenant from there, never from an argument. Middleware lists and runs
only the exposed tools. The tools are the chat's and the reading room's own reads (`retrieval.retrieve`, `library.list_sources`/
`passages_of`), `top_k` capped at `vector_store.top_k`, and call no model; results say passage text is data. Logs carry
the tool name and outcome only; FastMCP's spans carry names and ids, and arguments are already in
`DROPPED_ATTRIBUTE_PREFIXES`. RagView adds a "Connect via MCP" tab (`McpConnectView.tsx`: endpoint, client snippets, a
live `tools/list` with the session token). The chat itself has `list_sources`/`read_document` beside `retrieve_docs`
(`build_document_tools`), and a scenario with `mcp.consume` gets a "📚 Direct · 🔌 Via MCP" switch: `forwardedProps.route:
"mcp"` makes `agui_endpoint` load the tools from those doors instead (`core/mcp_consumer.py`: only doors this instance
serves, over loopback, with the caller's token, `mcp__<server>__<tool>`, `MAX_MCP_AGENT_STEPS`), and `chatProvenance.ts`
labels the reply "🔌 MCP". MCP clients on the local cluster use `make k3s-mcp-portforward` (`localhost:8031`, the
card's snippets): `*.localhost` does not resolve for every client. Nothing works before `make k3s-vectorize`.

A `data_product` opens the same kind of door on **data-api** (`mcp.expose` from `MCP_DATA_TOOLS`): `core/mcp_door.py`'s
`McpDoor` settles product, caller and `identity.access_for` (the REST dependency's own code: role, row filter, masks,
X-Data-View-As for platform keys) before MCP runs, and each tool is a REST route's core call (`metrics.query_metric`,
`records.*`, `governed_sql.run`; `get_record` publishes the same "served" event). `run_sql` is listed only when the role
may run SQL. DataProductView's "Connect via MCP" tab reuses `McpConnectView` with data-api's URL, `localhost:8032` and the
View-as headers. OAuth for external clients,
`data-api`, models as tools and tool policy are later steps of the ADR.
