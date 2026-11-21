# 0025 — Level 3: regulated MCP servers

Source: docs/adr/0025-level-3-regulated-mcp-servers.md (this repository)

Builds on ADR 0022 (the MCP server kit and per-API tokens) and ADR 0024 (one MCP catalog).

**Context.** Level 2 gives each API a token valid only there, for one user, for minutes. Regulated deployments
(banking, insurance) also ask that a copied token be useless, that a signed-out user stop at once instead of
when their token expires, that services authenticate each other, and that data an API needs not travel inside
tokens other parts can read. A spike against the framework's Keycloak (26.7.4) settled what is possible:

- Keycloak binds an exchanged token to the requesting client's key when the exchange carries a DPoP proof
  (`cnf.jkt`, `token_type: DPoP`), and refuses an exchange without one once `dpop.bound.access.tokens` is set.
- Keycloak **refuses to exchange a DPoP-bound token for any client other than the one it was issued to**
  ("Sender-constrained token exchange rejected"). Binding the user's token (T1) to the chat UI would forbid the
  per-API exchange, and the chat UI (Open WebUI) cannot sign proofs anyway.
- Introspection answers only to a client in the token's audience: the MCP server can check T1, the API T2.
  Signing a user out makes T1 and every T2 exchanged from it inactive at once.
- Keycloak offers no encrypted access tokens. A **lightweight** access token keeps only claims flagged for it,
  and introspection returns the rest; the tenant mapper must be flagged, or introspection drops it too.
- Keycloak merges client attribute updates: a flag removed from a file stays on in a live realm.

**Decision.** Level 3 is opt-in per kit server (`level: 3` in `mcp/catalog.yaml`), fails closed, and the
`crm-mcp` + `crm-api` example runs at it.
