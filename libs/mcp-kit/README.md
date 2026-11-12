# mcp-kit

The MCP server kit (docs/adr/0022): token validation, the access policy, the per-API token exchange,
a tool context that never exposes a token, and one audit line per call, so an MCP server in front of an
existing API is its tools and nothing else. `mcp_kit.testing` replays the blueprint's failure cases.
