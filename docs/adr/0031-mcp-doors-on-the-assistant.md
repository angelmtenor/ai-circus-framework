# 0031 — MCP doors on the assistant: a tabular scenario's model, read-only

Builds on ADR 0019 (MCP doors), ADR 0018 (several models per scenario), ADR 0024 (the MCP catalog) and ADR 0030
(the drought watch building blocks).

**Context.** Document scenarios (rag-agent) and data products (data-api) already open an MCP door, so an external
chat, an IDE or another agent can use them with the caller's own token. Tabular scenarios, the ones with a trained
model, could not: `MCP_TOOLS_BY_KIND` had no `tabular_ml` entry. Those are the scenarios a client asks about most
("can our own assistant use this model?"), and their tools already exist inside the in-app chat.

**Decision.**
- **The assistant serves the door**, at `/mcp/{scenario_slug}` on its existing app and ingress
  (`assistant.localhost`), the same shape as ADR 0019: one stateless FastMCP server for every tabular scenario that
  declares `mcp:`, no new pod and no new memory. `prediction` stays a pure scoring API with no MCP of its own.
- **The tools are the chat's, without a model in between.** Two that fit any tabular scenario, and seven that need
  `analysis_toolset: drought_watch`:
  - `describe_model()`: what the model predicts, from which inputs (type, range, options, default) and its model card.
  - `score_record(record)`: one record scored by the deployed model, what the number means (a classifier's probability
    of the positive class, or a regressor's value), its eight largest SHAP drivers, its 90% interval when there is one,
    and, for a scenario with business rules, the gate, the decision and the rules that fired: a record a rule blocks is
    still scored, but the decision is the rule's.
  - `drought_state`, `storage_forecast`, `what_if`, `least_restrictive_measures`, `search_drought_plan`, `data_quality`,
    `weekly_bulletin`: the same `DroughtToolkit` the chat and the drought control room read.
- **The schema decides what a scenario may expose.** `score_record` only for a classifier or a regressor (not a
  forecast or a recommender), the drought tools only with the drought toolset, at most nine tools. A model view (ADR
  0018) never carries the door: it belongs to the scenario, and `score_record` scores the scenario's main model.
- **Identity is the existing one.** `McpDoor` settles the scenario (404 when it has no door) and the caller (401/403
  from `resolve_caller_identity`) before the MCP layer runs. The caller's bearer token is forwarded unchanged to
  `prediction`, which checks it again, so the tenant always comes from the token and never from a tool argument.
- **Read-only and bounded.** Every tool is annotated read-only; nothing is written. A record has at most the scenario's
  features (an unknown name is an error that points to `describe_model`), text values are capped, and inputs left out
  take the scenario's defaults. Each result says that it is decision support and that a person decides.
- **Wiring is the catalog's.** Each door is a `scenario_door` in `mcp/catalog.yaml` (gateway route, chat UI
  connections); `make k3s-mcp-portforward` puts the assistant on `localhost:8034` for clients on this machine. The
  workspace gains a **Connect via MCP** tab (analyst and engineer views) with the endpoint, the tools, client snippets
  and a live `tools/list` check, the same view as the document and data doors.
- **First doors:** `water_reservoirs_drought` (`describe_model` and the drought tools; its model is a forecast, so no
  `score_record`), `financial_crime_desk` and `insurance_portfolio` (`describe_model`, `score_record`).

**Consequences.**
- A tabular scenario's model is usable from LibreChat, Open WebUI, Claude Code or any MCP client, with the same
  tenancy, entitlement and traces as the app; the client's own model does the reasoning.
- Every image that validates tabular scenario files must know the new `mcp:` rules before a tabular YAML declares one:
  an older image rejects a tabular `mcp:` block (ADR 0019's validator refused the kind).
- The drought tools still read the scenario's committed synthetic files, as the chat does; a tenant's own artifacts are
  a later step.
- No write tools: a tool that changes state (open a case, record a decision) would need the access policy and audit of
  an MCP server built with `libs/mcp-kit` (ADR 0022) in front of an API, not a scenario door.
