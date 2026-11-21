# 0022 — The MCP server kit and per-API tokens

Source: docs/adr/0022-mcp-server-kit-and-per-api-tokens.md (this repository)

**Verified on k3s** with real Keycloak tokens of the `lite-demo` users. Keycloak's exchange gives
`crm-mcp` a T2 whose only audience is `crm-api`, for the same user, tenant, role and store claim; asking for
any other audience, or exchanging as a client the token is not addressed to, fails with 400. At crm-api, the
user's T1 replayed directly gets 401; a request naming another user gets 403; the analyst's T2 sees all 27
customers, the S012 store manager's sees 4, all in S012, and gets 404 for a customer of another store. Through
the gateway and the chat UI, crm-mcp lists 3 tools for the analyst and 2 for the store manager (no
`top_customers`), and in the chat the store manager's question is answered "You can see 4 customers, all of
whom are associated with store S012". Audit lines carry the tool, outcome, pseudonymous subject and tenant id.
Open WebUI shows a kit server's tools as `crm_crm-<tool>` (its connection id plus the gateway's prefix), and
gemini-flash sometimes misnames them; the doors are unaffected.

**Consequences.** A new API needs a generated server, its tools, a `policy.yaml`, two Keycloak clients and
one gateway entry; the security is inherited. Level 3 (DPoP, mutual TLS, JWE claims) and generating the
policy, gateway entries and Keycloak clients from one catalog are later steps (the catalog: ADR 0024).
