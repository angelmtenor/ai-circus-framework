# 0007 — One governed `data-api`; assistant data tools; MCP on the same service

**Context.** The proposal had separate `data-api` and `data-mcp` services, and a "four doors"
principle: REST, MCP, SQL, console — the same controls behind each.

**Decision.** `data-api` is the single enforcement point: records by key (bronze + processing
trail), search, scenario-declared metrics (whitelisted identifiers, parameterised SQL, row
limits), governed read-only SQL over declared datasets (admin/analyst roles), dataset catalogue
with freshness/quality, pipeline events over SSE, simulator controls, and an MCP endpoint
(streamable HTTP) exposing the same operations as tools. The `assistant` gets data tools for
`data_product` scenarios that call `data-api` with the caller's token.

**Consequences.** One small pod instead of two; MCP and REST cannot drift apart. External
agents use MCP; the in-app chat uses the assistant (LangGraph + AG-UI, traced in Langfuse).
