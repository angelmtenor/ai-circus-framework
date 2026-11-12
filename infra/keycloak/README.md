# Keycloak realm bootstrap

`Dockerfile` builds the platform's Keycloak image (`ai-circus/keycloak:local`, via
`make k3s-build` or docker compose): `kc.sh build` runs once at image build, so the container
starts with `start --optimized` and skips Quarkus re-augmentation on every boot. The version
pin (`KEYCLOAK_VERSION`) lives there; DB migrations run automatically and are forward-only.

`realm-export.json` is a declarative realm bootstrap, baked into that image and loaded by
`--import-realm` on first boot (skipped once the realm exists) — the single copy both
`k8s/base/keycloak.yaml` and `docker-compose.yml` use.

It defines the `ai-circus` realm with `organizationsEnabled: true`, the built-in
`organization` client scope (Organization Membership protocol mapper, included in the
token), a `platform-backend` client scope carrying an Audience mapper set to
`https://api.ai-circus-framework.local` (the value `KEYCLOAK_AUDIENCE` must match — see
`.env.example`), and a public `ui-react` SPA client (PKCE, standard flow, redirect URIs
for both the Traefik-served build at `http://aiopen.localhost` and the Vite dev server
at `http://localhost:5173`).

**This bootstrap is unverified against a live Keycloak instance** — Phase 0 of the
Logto→Keycloak migration plan was authored without a running Keycloak to import it
into. Before relying on it, validate via the `k3s-deploy-verify` skill: bring up the
cluster, confirm `--import-realm` actually produces a realm with `organizationsEnabled`
true, the `organization` scope present with its mapper active in issued tokens, the
`platform-backend` scope's `aud` claim showing up, and a real PKCE login round-trip
against the `ui-react` client. Also unverified: the M2M service-account's
`manage-users`/`manage-organizations`/`manage-realm`/`manage-clients` realm-management
role grants (Phase 4's sync tooling) — those roles have no representation in this
realm-export at all; they must be assigned once, after realm import, using the
container's own `KEYCLOAK_ADMIN_USERNAME`/`KEYCLOAK_ADMIN_PASSWORD` bootstrap admin
credentials (see the naming contract) — a mechanism that also has not been exercised
live.

## Keeping a live realm in step (docs/adr/0021)

`--import-realm` applies this file only on a realm's first boot, so a cluster created earlier never
sees a change to it. `scripts/chat_ui_identity.py` (run by `make k3s-chat-ui-up`, safe to re-run)
brings the live realm up to the file through `kcadm.sh` and the pod's own bootstrap admin: missing
client scopes, protocol mappers and their config, realm roles, clients and default client scopes,
plus the chat UI client's secret from `.env` and the `lite-demo` demo tenant.

An imported realm gets none of Keycloak's built-in scopes, so this file declares the ones a token
needs: `basic` (`sub`), `profile`, `email`, `roles` (`realm_access.roles`) and `data-access` (row-filter
claims such as `stores`, from an admin-only user attribute declared in the user profile). The
organization mapper adds the organization id, giving the `{alias: {id}}` claim `ai_circus_shared.auth`
reads. `libs/shared/tests/test_keycloak_realm.py` pins all of it: verified live with real sign-ins.

`KC_HOSTNAME` is the full public URL with `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`: a client inside
the cluster gets in-cluster token and JWKS URLs, while the issuer and login pages stay public.

MCP servers in front of an API (docs/adr/0022): each is a confidential client with standard token exchange
(`standard.token.exchange.enabled`) and exactly one audience scope, its API (`crm-mcp` → `crm-api-audience`). The
`mcp-servers` scope puts every such client in the user token's audience, which Keycloak requires before a client
may exchange a token; impersonation is never granted. `libs/shared/tests/test_keycloak_realm.py` pins both.
