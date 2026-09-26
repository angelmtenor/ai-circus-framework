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
