# 0022 — The MCP server kit and per-API tokens

Builds on ADR 0020 (MCP gateway) and ADR 0021 (real Keycloak sign-in).

**Context.** The framework's own MCP doors are also the APIs they serve. An organisation's APIs are not:
each needs an MCP server in front of it, and that server must never become a way to act as another user.
Passing the user's token straight through to the API (pass-through) works, but a token sent to one API is
then valid at every other API that accepts the same audience, and every API trusts whatever the MCP layer
forwards. Writing the security into each MCP server by hand would drift.

**Decision.**
- **A kit, `libs/mcp-kit` (package `mcp_kit`)**, kept apart from `ai-circus-shared` so the standalone stack
  can ship it. It holds everything an MCP server in front of an API needs for security, so the server's own
  code is its tools only:
  - validation of the user's token (T1): issuer, signature, expiry, audience = this server, exactly one
    tenant; 401 otherwise, before any MCP handling (rules R1, R3);
  - the access policy (`policy.yaml`: tools per realm role, optionally per tenant): `tools/list` shows only
    the granted tools, other calls are refused, and a caller with no tool gets 403;
  - the per-API token (T2): RFC 8693 token exchange at Keycloak for the API's audience only, cached per T1
    until shortly before expiry, bounded (R4, R5);
  - `kit.context()`: the caller's identity, read-only, and an API client that already holds T2. Tool code
    never sees a token and tools take no identity arguments (R8). The client forwards `traceparent`;
  - one audit line per call (server, tool, outcome, pseudonymous subject, tenant id): never arguments,
    results or tokens (R9);
  - `mcp_kit.testing`: a throwaway signing key and an identity-provider stand-in, so every server replays
    the blueprint's failure cases.
- **Keycloak standard token exchange (26.x), no impersonation.** Each MCP server is a confidential client
  with `standard.token.exchange.enabled` and exactly one audience scope (its API). A `mcp-servers` scope puts
  every MCP server client in the user token's audience, which Keycloak requires before a client may
  exchange a token. Impersonation is never granted. A test pins that each exchanging client can reach one
  audience, has no sign-in flow and does not carry the platform audience.
- **An example pair, optional** (`k8s/mcp-examples/`, `make k3s-mcp-examples-up`, compose profile
  `mcp-examples`; scaffolded services): `crm-api`, a customer API that knows nothing about MCP (accepts only
  tokens issued for it, takes user and tenant from the token, refuses a request that names someone else with
  403, filters rows by role and masks personal fields), and `crm-mcp`, the server built with the kit. The MCP
  gateway lists it as `crm` (`/mcp/crm`, passthrough) and the chat UI connects to it like the scenario doors.
- **The gateway contract** now accepts two kinds of door: a scenario's (`/mcp/{slug}`) and a kit server's
  (`/mcp/` on a service whose project depends on `mcp-kit`).

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
policy, gateway entries and Keycloak clients from one catalog are later steps.
