# 0024 — One catalog for the MCP path

Source: docs/adr/0024-one-mcp-catalog.md (this repository)

**Consequences.** Adding an API is one catalog entry plus one scaffolded server with its tools; the routes, chat
connection, policy and Keycloak clients follow, and CI fails if any of them drifts. Removing a server removes its
route, connection and Keycloak entries. A standalone stack takes the catalog, the generator and the portal as is,
pointing the generator at its own files.
