# Architecture details - The MCP door (docs/adr/0019)

Source: docs/architecture-details.md (this repository)

**One catalog for the whole path** (docs/adr/0024): `mcp/catalog.yaml` lists the gateway's address, every server
(`scenario_door`: a scenario's own door, whose tools its entitlements and `scenario.yaml` roles decide, so no group lists
it; `kit_server`: service, API, tenants, tools) and every group (a realm role → tools per kit server, or `"*"`).
`scripts/generate_mcp_catalog.py` writes the gateway's `mcp_servers`, both chat UIs' connections (Open WebUI's in k8s and
compose, LibreChat's `mcpServers` in `k8s/chat-ui-librechat/librechat-config.yaml`, and the lite profile's kit-only
variants in `k8s/mcp-lite/`), each kit
server's `policy.yaml` and the Keycloak entries (exchange client, API client, `<api>-audience` scope, `mcp-servers` mapper,
all marked "Generated from mcp/catalog.yaml."); hand-written files keep their comments around `BEGIN`/`END` markers. It
refuses a catalog that would route, grant or exchange by mistake (names with `-`, undeclared doors, credential headers,
tools other than the server's `@kit.tool()` functions, grants on doors, groups that are not realm roles, `$` or `{{` in a text).
`make mcp-catalog` writes, `make mcp-catalog-check` and `libs/shared/tests/test_mcp_catalog.py` fail on drift, `make
k3s-mcp-catalog-apply` syncs Keycloak and rebuilds llm-gateway and the kit servers. A kit server's secret is
`<CLIENT_ID>_CLIENT_SECRET` in `.env` (`chat_ui_identity.py` derives it). `make mcp-catalog-portal` serves a local page
(127.0.0.1, per-run token, nonce CSP) to tick tools per group, add a server, review the diffs and publish (= the generator;
never deploys or commits). Chat UI visibility per group is not generated: Open WebUI grants by internal group ids, and
LibreChat shows every server to every user.

**Level 3, for regulated deployments** (docs/adr/0025): `level: 3` on a kit server of the catalog. The per-API token
is DPoP-bound to a key the MCP server holds in memory only (`mcp_kit.DpopKey`): proof on the exchange, a fresh proof on
every API call; the API checks method, URL, freshness, the token hash, a never-seen `jti` and the bound key, so a copied
token is useless. The user's token stays a bearer token (Keycloak refuses to exchange a sender-constrained token for
another client) and is introspected on every request; the API introspects its own token too, so signing a user out
stops both at once, and an unreachable Keycloak is a refusal (503), never a pass. The per-API token is lightweight
(ids only) and the API reads roles and the store claim from introspection. One CA per server/API pair
(`scripts/mcp_tls_certs.py`, `make k3s-mcp-tls`, `infra/mcp-tls/` gitignored) gives mutual TLS on the API port; a
plain port serves only `/healthz` and `/metrics`. The catalog writes the Keycloak flags explicitly both ways (Keycloak
merges attribute updates). Not covered: gateway → MCP server mutual TLS (LiteLLM's open-source MCP client cannot present
a client certificate) and a DPoP-bound user token. The `crm` example runs at level 3.
