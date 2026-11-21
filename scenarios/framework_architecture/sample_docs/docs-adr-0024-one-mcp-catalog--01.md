# 0024 — One catalog for the MCP path

Source: docs/adr/0024-one-mcp-catalog.md (this repository)

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
