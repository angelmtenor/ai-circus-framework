# MCP Lite Stack - Add an API

Source: docs/mcp/README.md (this repository)

1. Scaffold the MCP server: `make new-service NAME=<api>-mcp`, add `mcp-kit` as a dependency and write its tools
   with `@kit.tool()` (the kit does tokens, policy, exchange and audit). The API itself only has to accept a token
   whose audience is its own client.
2. Add a `kit_server` entry to `mcp/catalog.yaml` (service, API, tools, optional tenants and `level: 3`) and grant
   tools to groups (realm roles). `make mcp-catalog-portal` does the same in a local page.
3. `make mcp-catalog` regenerates the gateway route, both chat UIs' connections, the server's `policy.yaml` and the
   Keycloak clients (exchange client, audience scope, pseudonym mapper); commit them together.
4. `make k3s-mcp-catalog-apply` puts it on the running cluster (`make k3s-mcp-tls` first at Level 3).
