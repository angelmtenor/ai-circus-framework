# 0025 — Level 3: regulated MCP servers

Source: docs/adr/0025-level-3-regulated-mcp-servers.md (this repository)

- **The per-API token is DPoP-bound** (RFC 9449). The MCP server holds an EC P-256 key in memory only
  (`mcp_kit.DpopKey`, a new one per process), signs a proof when it exchanges T1 and a fresh one for every API
  call (`Authorization: DPoP`, `ath` = the token's hash). It refuses an exchange answer not bound to its key.
  The API checks the proof's type, algorithm and public key, the request's method and URL, freshness, the token
  hash, that it has never seen the proof's `jti`, and that the key is the one the token names. A T2 copied out
  of a log or a proxy is useless without that key.
- **T1 stays a bearer token, checked live on every request** (RFC 7662): the kit introspects it as the MCP
  server and refuses an inactive token (401) or an unanswered check (503). The API introspects T2 the same way.
- **Mutual TLS between the MCP server and its API**, with one small certificate authority per pair
  (`mcp_kit.pki`, `scripts/mcp_tls_certs.py`, `make k3s-mcp-tls`): it signs the API's server certificate
  (serverAuth) and the MCP server's client certificate (clientAuth), and each side trusts only it. The API serves
  only mutual TLS on its API port; a plain port serves `/healthz` and `/metrics` for probes and the scraper.
  Each workload's Secret holds only its own key. Keys live in `infra/mcp-tls/` (gitignored) and are never printed.
- **Claims only the API reads**: T2 is a lightweight token carrying ids (subject, audience, tenant, key binding);
  the API takes roles and row filters (the store claim) from its own introspection, so nothing between Keycloak
  and the API can read them from T2. This is Keycloak's native equivalent of encrypting those claims (JWE).
- **The catalog drives it**: `level: 3` writes `level: 3` into the server's `policy.yaml` (the kit then refuses
  to start without an introspector, a DPoP key and a mutual-TLS `https` API), and sets the Keycloak flags
  explicitly both ways (`dpop.bound.access.tokens`, `client.use.lightweight.access.token.enabled`, the audience
  mapper's `lightweight.claim`), never by omission. Every catalog client gets `<CLIENT_ID>_CLIENT_SECRET`; at
  Level 3 the API's introspects its tokens.
- **Observability**: `mcp_kit_introspections_total{server,outcome}` and its latency; refusals `inactive` and
  `introspection_unavailable`; an alert when a signed-out session's token is used again.

**Not covered, on purpose or for now.** T1 is not sender-constrained (see above); a deployment whose chat UI and
MCP clients sign DPoP proofs would also need Keycloak to accept exchanging them. The gateway → MCP server hop
has no mutual TLS: LiteLLM's open-source MCP client verifies a server certificate but cannot present a client
certificate, and the framework does not patch third-party code; a service mesh closes that hop. Keycloak is
reached over plain HTTP inside the lab cluster; a production Keycloak terminates TLS itself, and could then
authenticate MCP servers by certificate (`tls_client_auth`) instead of a client secret. The scenario doors
(`rag-agent`, `data-api`) stay at their own level: they read claims from T1 locally.

**Consequences.** A Level 3 server costs one introspection per request and per API call (a few milliseconds
in-cluster), a certificate renewal every 90 days (`make k3s-mcp-tls`, then restart the pair) and an API that
checks proofs. A token copied anywhere on the path is refused everywhere; a signed-out user is refused on their
next call.
