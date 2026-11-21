# 0031 — MCP doors on the assistant: a tabular scenario's model, read-only

Source: docs/adr/0031-mcp-doors-on-the-assistant.md (this repository)

**Consequences.**
- A tabular scenario's model is usable from LibreChat, Open WebUI, Claude Code or any MCP client, with the same
  tenancy, entitlement and traces as the app; the client's own model does the reasoning.
- Every image that validates tabular scenario files must know the new `mcp:` rules before a tabular YAML declares one:
  an older image rejects a tabular `mcp:` block (ADR 0019's validator refused the kind).
- The drought tools still read the scenario's committed synthetic files, as the chat does; a tenant's own artifacts are
  a later step.
- No write tools: a tool that changes state (open a case, record a decision) would need the access policy and audit of
  an MCP server built with `libs/mcp-kit` (ADR 0022) in front of an API, not a scenario door.
