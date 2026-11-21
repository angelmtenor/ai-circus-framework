# MCP Lite Stack - Operate it

Source: docs/mcp/README.md (this repository)

| Task | Command |
|---|---|
| Who may run what | `make mcp-catalog` prints the matrix after writing |
| Catalog drift (what CI runs) | `make mcp-catalog-check` |
| Renew Level 3 certificates (every 90 days) | `make k3s-mcp-tls`, then restart the pair |
| Which pseudonym an API sees for a user | `make k3s-mcp-pseudonym WHO=<username>` |
| Who a pseudonym from an API's log is | `make k3s-mcp-pseudonym FIND=<pseudonym>` (Keycloak admin access) |
| The MCP dashboard | Grafana → "AI Circus · MCP servers": calls, refusals, per-API tokens, live checks, audit lines |

<p align="center"><img src="../screenshots/mcp/grafana-mcp-dashboard.webp" alt="Grafana MCP dashboard: tool calls, calls outside the grant, refusals, per-API tokens, exchange round trip, live token checks and audit lines" width="850"></p>
