# mcp-kit

The MCP server kit (docs/adr/0022): token validation, the access policy, the per-API token exchange,
a tool context that never exposes a token, and one audit line per call, so an MCP server in front of an
existing API is its tools and nothing else. `mcp_kit.testing` replays the blueprint's failure cases.

**Level 3** (docs/adr/0025, `level: 3` in the server's generated `policy.yaml`): the kit also checks the user's
token live at the identity provider on every request (`TokenIntrospector`), binds each per-API token to a key
held only in memory and signs a fresh DPoP proof for every API call (`DpopKey`), and reaches the API over
mutual TLS (`ApiTls`, certificates from `mcp_kit.pki`). A server configured at level 3 without any of these
refuses to start (`Level3ConfigError`). `mcp_kit.testing` adds an identity provider that binds, introspects
and revokes, a proof checker for stand-in APIs and throwaway certificates (`write_test_pki`).
