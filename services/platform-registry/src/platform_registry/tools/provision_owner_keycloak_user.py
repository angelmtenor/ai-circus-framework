"""
provision_owner_keycloak_user.py
----------------------------------

One-off, idempotent provisioning: ensures the Audience client scope + protocol mapper
exist, creates (or finds) every `scenario:<slug>` realm role, creates (or finds) ui-react's
SPA client (the OIDC client its "Log in with Keycloak" button needs), creates (or finds)
a real Keycloak Organization and a real Keycloak user (`KEYCLOAK_OWNER_EMAIL`/
`KEYCLOAK_OWNER_PASSWORD`), adds that user to the organization, assigns it every
`scenario:<slug>` realm role, and syncs the result into the local `entitlements` table —
so signing in through Keycloak's login page with that email lands on every scenario, the
same way the `ADMIN_API_KEY` bypass already does.

Unlike `provision_owner_user.py`'s own docstring claim for its Logto 1.42 tenant, this
Keycloak port has **not** been exercised against a live Keycloak instance (none was
available in this pass) — every Admin REST request/response shape here follows Keycloak's
documented Admin REST API reference and current GitHub issues, not a live run. Re-verify
against a real deployment (see the `k3s-deploy-verify` skill) before relying on this in
production, the same "best-effort" caveat `sync_keycloak_entitlements.py` already carries.

Run manually (`make provision-owner-user`), any time after the one-time service-account
privilege bootstrap (the M2M client's service-account user needs the
`manage-users`/`manage-organizations`/`manage-realm`/`manage-clients` realm-management
client roles assigned via the realm's own bootstrap admin credentials — see
`sync_keycloak_entitlements.py`'s docstring; this has no Logto analog) — including again
after a `make reset-all`, once that bootstrap has been redone and
`KEYCLOAK_M2M_CLIENT_ID`/`SECRET` are back in `.env`.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

from pathlib import Path

import httpx
from ai_circus_shared.scenario_schema import load_all
from sqlalchemy.orm import Session

from platform_registry import get_env_config, get_logger
from platform_registry.core.db import init_engine
from platform_registry.tools.sync_keycloak_entitlements import admin_request, fetch_m2m_token, sync_entitlements

logger = get_logger(__name__)

OWNER_ORG_NAME = "owner"
# The client's `clientId` (public identifier used by the OIDC library) — distinct from
# Keycloak's internal UUID `id`, which every Admin REST call below addresses it by.
UI_CLIENT_ID = "ai-circus-ui-react"
# ui-react's two local dev origins — the Traefik-served build and the Vite dev
# server (see ui-react/src/config.ts's own fallback defaults / docker-compose.yml's
# CORS_ALLOWED_ORIGINS default).
UI_REDIRECT_ORIGINS = ["http://aiopen.localhost", "http://localhost:5173"]
# New client scope (no Logto analog — Logto's "API resource" registration doesn't exist
# here) whose Audience protocol mapper is what puts KEYCLOAK_AUDIENCE into the access
# token's `aud` claim, since Keycloak omits `aud` by default.
AUDIENCE_CLIENT_SCOPE_NAME = "platform-backend"


def ensure_audience_client_scope(client: httpx.Client, audience: str) -> None:
    """Idempotently ensure the `platform-backend` client scope + Audience mapper exist.

    If Phase 0's realm-export.json already declares this scope on realm import, this is
    just a confirm/no-op (GET first, POST only if missing) — matching the existing tools'
    idempotent GET-then-POST pattern. Only creates anything on a realm that doesn't already
    have it (e.g. one bootstrapped by hand rather than via `--import-realm`).
    """
    scopes = admin_request(client, "GET", "/client-scopes").json()
    scope = next((s for s in scopes if s["name"] == AUDIENCE_CLIENT_SCOPE_NAME), None)
    if scope is None:
        admin_request(
            client,
            "POST",
            "/client-scopes",
            json={
                "name": AUDIENCE_CLIENT_SCOPE_NAME,
                "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"},
            },
        )
        scopes = admin_request(client, "GET", "/client-scopes").json()
        scope = next((s for s in scopes if s["name"] == AUDIENCE_CLIENT_SCOPE_NAME), None)
        if scope is None:
            raise RuntimeError(f"Created client scope {AUDIENCE_CLIENT_SCOPE_NAME} but couldn't find it on re-fetch")
        logger.info("Created client scope {}", AUDIENCE_CLIENT_SCOPE_NAME)

    mappers = admin_request(client, "GET", f"/client-scopes/{scope['id']}/protocol-mappers/models").json()
    if any(m["protocolMapper"] == "oidc-audience-mapper" for m in mappers):
        return
    # `included.custom.audience` (a fixed string), not `included.client.audience` (which
    # targets another client's clientId) — inferred from the oidc-audience-mapper's
    # documented config keys, not exercised live.
    admin_request(
        client,
        "POST",
        f"/client-scopes/{scope['id']}/protocol-mappers/models",
        json={
            "name": "audience-mapper",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "config": {
                "included.custom.audience": audience,
                "id.token.claim": "false",
                "access.token.claim": "true",
            },
        },
    )
    logger.info("Added Audience mapper ({}) to client scope {}", audience, AUDIENCE_CLIENT_SCOPE_NAME)


def ensure_realm_roles(client: httpx.Client, role_names: set[str]) -> None:
    """Create any `scenario:<slug>` realm role that doesn't already exist.

    Realm roles replace Logto's org-scoped organization roles here — Keycloak's
    Organizations resource has no org-scoped roles endpoint (see
    `sync_keycloak_entitlements.py`'s docstring), so scenario entitlements are modeled as
    plain realm roles assigned per-user instead.
    """
    existing = {r["name"] for r in admin_request(client, "GET", "/roles").json()}
    for name in role_names - existing:
        admin_request(client, "POST", "/roles", json={"name": name})
        logger.info("Created realm role {}", name)


def ensure_spa_client(client: httpx.Client, client_id: str, redirect_origins: list[str]) -> str:
    """Return the internal id of the SPA client `client_id`, creating it if missing.

    Public client, PKCE-only (S256), standard (authorization code) flow — a wildcard
    redirect URI + web origin per origin in `redirect_origins`. react-oidc-context (Phase 5)
    handles the OIDC callback inside `AuthProvider` rather than a dedicated `/callback`
    route, so a same-origin wildcard is used here rather than a specific callback path —
    inferred from Keycloak's own react-oidc-context sample project, not exercised live.
    """
    matches = admin_request(client, "GET", "/clients", params={"clientId": client_id}).json()
    for existing_client in matches:
        if existing_client["clientId"] == client_id:
            return existing_client["id"]
    # POST /clients returns 201 with no body (id comes back only via the Location header),
    # so the id is recovered with a follow-up GET rather than read off the response.
    admin_request(
        client,
        "POST",
        "/clients",
        json={
            "clientId": client_id,
            "protocol": "openid-connect",
            "publicClient": True,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,
            "redirectUris": [f"{origin}/*" for origin in redirect_origins],
            "webOrigins": redirect_origins,
            "attributes": {"pkce.code.challenge.method": "S256"},
        },
    )
    created = admin_request(client, "GET", "/clients", params={"clientId": client_id}).json()
    for new_client in created:
        if new_client["clientId"] == client_id:
            logger.info("Created SPA client {} ({})", client_id, new_client["id"])
            return new_client["id"]
    raise RuntimeError(f"Created client {client_id} but couldn't find it on re-fetch")


def ensure_organization(client: httpx.Client, name: str) -> str:
    """Return the id of the Organization named `name`, creating it if missing.

    Keycloak's organization create body requires a non-empty `domains` array (unlike
    Logto's bare `{"name": ...}`) — `{name}.internal` is a synthetic identifier, not a real,
    verifiable email domain; Keycloak's schema just happens to require one.
    """
    organizations = admin_request(client, "GET", "/organizations").json()
    for org in organizations:
        if org["name"] == name:
            return org["id"]
    # As with clients, POST /organizations returns 201 with no body — recover the id via
    # a follow-up GET.
    admin_request(
        client,
        "POST",
        "/organizations",
        json={"name": name, "domains": [{"name": f"{name}.internal", "verified": True}]},
    )
    created = admin_request(client, "GET", "/organizations").json()
    for org in created:
        if org["name"] == name:
            logger.info("Created organization {} ({})", name, org["id"])
            return org["id"]
    raise RuntimeError(f"Created organization {name} but couldn't find it on re-fetch")


def ensure_user(client: httpx.Client, email: str, password: str) -> str:
    """Return the id of the Keycloak user with email `email`, creating it if missing.

    Unlike a Logto Management-API-created user (email-only, no username), Keycloak
    requires a `username` on create — this reuses `email` as the username, the common
    Keycloak convention for realms that don't separately manage usernames.
    """
    matches = admin_request(client, "GET", "/users", params={"email": email, "exact": "true"}).json()
    for user in matches:
        if user.get("email") == email:
            return user["id"]
    admin_request(
        client,
        "POST",
        "/users",
        json={
            "username": email,
            "email": email,
            "enabled": True,
            "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        },
    )
    created = admin_request(client, "GET", "/users", params={"email": email, "exact": "true"}).json()
    for user in created:
        if user.get("email") == email:
            logger.info("Created user {} ({})", email, user["id"])
            return user["id"]
    raise RuntimeError(f"Created user {email} but couldn't find it on re-fetch")


def ensure_org_membership(client: httpx.Client, org_id: str, user_id: str) -> None:
    """Add `user_id` to `org_id` if not already a member (idempotent no-op otherwise).

    Unusual, unverified-live API shape: unlike every other write in this module, the
    membership POST body is the raw user-id string, not a JSON object — confirmed against
    Keycloak's Admin REST API reference and current GitHub issues, but not exercised
    against a live server.
    """
    members = admin_request(client, "GET", f"/organizations/{org_id}/members").json()
    if any(m["id"] == user_id for m in members):
        return
    admin_request(
        client,
        "POST",
        f"/organizations/{org_id}/members",
        content=user_id,
        headers={"Content-Type": "text/plain"},
    )
    logger.info("Added user {} to organization {}", user_id, org_id)


def assign_scenario_roles(client: httpx.Client, user_id: str, role_names: set[str]) -> None:
    """Assign every `scenario:<slug>` realm role in `role_names` to `user_id`.

    Keycloak's Organizations resource has no org-scoped role-assignment endpoint (see
    `sync_keycloak_entitlements.py`'s docstring) — scenario roles are assigned directly to
    the user via the realm role-mappings endpoint, independent of organization membership.
    """
    roles_by_name = {r["name"]: r for r in admin_request(client, "GET", "/roles").json()}
    current = {r["name"] for r in admin_request(client, "GET", f"/users/{user_id}/role-mappings/realm").json()}
    missing = role_names - current
    if not missing:
        return
    representations = [roles_by_name[name] for name in missing if name in roles_by_name]
    admin_request(client, "POST", f"/users/{user_id}/role-mappings/realm", json=representations)
    logger.info("Assigned {} scenario role(s) to user {}", len(representations), user_id)


def main() -> None:
    """CLI entry point: provision the owner Organization/user and sync its entitlements."""
    config = get_env_config()
    if not (
        config.KEYCLOAK_SERVER_URL
        and config.KEYCLOAK_REALM
        and config.KEYCLOAK_M2M_CLIENT_ID
        and config.KEYCLOAK_M2M_CLIENT_SECRET
    ):
        logger.error(
            "KEYCLOAK_SERVER_URL / KEYCLOAK_REALM / KEYCLOAK_M2M_CLIENT_ID / KEYCLOAK_M2M_CLIENT_SECRET "
            "must be set to run this tool."
        )
        raise SystemExit(1)
    if not (config.KEYCLOAK_OWNER_EMAIL and config.KEYCLOAK_OWNER_PASSWORD):
        logger.error("KEYCLOAK_OWNER_EMAIL / KEYCLOAK_OWNER_PASSWORD must be set to run this tool.")
        raise SystemExit(1)
    if not config.KEYCLOAK_AUDIENCE:
        logger.error("KEYCLOAK_AUDIENCE must be set to run this tool.")
        raise SystemExit(1)

    role_names = {d.role_required for d in load_all(Path(config.SCENARIOS_DIR))}

    token = fetch_m2m_token(
        config.KEYCLOAK_SERVER_URL,
        config.KEYCLOAK_REALM,
        config.KEYCLOAK_M2M_CLIENT_ID,
        config.KEYCLOAK_M2M_CLIENT_SECRET.get_secret_value(),
    )
    engine = init_engine(config)
    with (
        Session(engine) as session,
        httpx.Client(
            base_url=f"{config.KEYCLOAK_SERVER_URL}/admin/realms/{config.KEYCLOAK_REALM}",
            headers={"Authorization": f"Bearer {token}"},
        ) as client,
    ):
        ensure_audience_client_scope(client, config.KEYCLOAK_AUDIENCE)
        ensure_realm_roles(client, role_names)
        ensure_spa_client(client, UI_CLIENT_ID, UI_REDIRECT_ORIGINS)
        org_id = ensure_organization(client, OWNER_ORG_NAME)
        user_id = ensure_user(client, config.KEYCLOAK_OWNER_EMAIL, config.KEYCLOAK_OWNER_PASSWORD.get_secret_value())
        ensure_org_membership(client, org_id, user_id)
        assign_scenario_roles(client, user_id, role_names)

        result = sync_entitlements(session, client)

    slugs = result.get(org_id, set())
    logger.info(
        "Owner org {} ({}) is entitled to {} scenario(s): {}",
        OWNER_ORG_NAME,
        org_id,
        len(slugs),
        ", ".join(sorted(slugs)) or "(none)",
    )
    logger.info(
        "ui-react SPA client id: {} — set UI_REACT_KEYCLOAK_CLIENT_ID to this in .env, then "
        "'docker compose up -d --build ui-react' to bake it into the login screen's "
        "'Log in with Keycloak' button.",
        UI_CLIENT_ID,
    )


if __name__ == "__main__":
    main()
