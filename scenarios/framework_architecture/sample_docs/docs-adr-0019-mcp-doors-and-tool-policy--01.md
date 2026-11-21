# 0019 — MCP doors, starting read-only on a document scenario

Source: docs/adr/0019-mcp-doors-and-tool-policy.md (this repository)

Builds on ADR 0007 (MCP on `data-api`) and ADR 0008 (tenancy).

**Context.** Scenario capabilities should be usable as tools by any MCP client (an IDE, a desktop
assistant, another agent), with the same tenancy, entitlement and tracing as the REST doors. The
full picture (an MCP door on `data-api`, a generic server wrapping existing REST APIs, the in-app
assistant as an MCP client, per-role tool scoping) is too much to prove at once, and its first
step on `surf_retail` needs the Data Platform tiers running.

**Decision.**
- **MCP is a door, not a scenario kind.** A scenario opts in with an optional `mcp:` block; a
  scenario without it exposes nothing and behaves exactly as today.
- **First step: read-only documents on `conversational_rag`.** `rag-agent` serves one MCP endpoint
  per scenario, `/mcp/{scenario_slug}` (Streamable HTTP), on its existing app and ingress
  (`rag-agent.localhost`): no new pod, no new memory. Pilot: `ai_circus_reference`, whose documents
  are public notes with no personal data.
- **Three tools, all reads over what the scenario already indexed:**
  - `list_sources()` — the indexed documents and their passage counts.
  - `search_documents(query, top_k)` — the tenant's most similar passages, each with its source and score.
  - `read_document(source)` — one document's passages, in order.
  These reuse `ai_circus_shared.retrieval.retrieve` and `rag_agent.core.library`, the same code the
  chat's `retrieve_docs` tool and the reading room use. A scenario lists which of them it exposes:
  `mcp: { expose: [list_sources, search_documents, read_document] }`.
- **No model call inside the MCP server.** The client's own model reasons; the door only returns
  governed passages. Passage text stays delimited as untrusted content, as in `retrieve_docs`.
- **MCP does not replace indexing.** The tools read the tenant's Qdrant collection; before
  `make k3s-vectorize` they return empty results, like the reading room.
- **Identity is the existing one.** Each request carries `Authorization: Bearer …` (a Keycloak token
  or a demo API key) and goes through `resolve_caller_identity` for that scenario slug: 401/403
  before any tool runs. `org_id` comes from the token, never from a tool argument.
- **Bounded inputs.** `query` has a maximum length, `top_k` is capped at the scenario's
  `vector_store.top_k`, `read_document` returns at most the reading room's passage cap.
- **SDK: FastMCP v4, pinned to a minor version**, a layer on the official `mcp` SDK v2 (which
  supports the stateless `2026-07-28` revision) that mounts into the FastAPI app. It already has
  what the later steps need: a Keycloak auth provider (step 1), an OpenAPI provider (step 3),
  middleware for tool policy (step 4) and an in-memory client for tests. Cost: about twenty more
  transitive dependencies and a fast release cadence, hence the pin and `uv audit`.
- **Observability unchanged:** the endpoint lives inside the traced FastAPI app; logs and spans
  carry the tool name and outcome, never the query or passage text.
