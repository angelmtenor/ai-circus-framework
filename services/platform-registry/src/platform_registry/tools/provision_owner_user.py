"""
provision_owner_user.py
------------------------

One-off, idempotent provisioning: switches the tenant's Sign-in Experience to accept
email (Logto's default is username, which a Management-API-created user never has),
creates (or finds) a real Logto Organization and a real Logto user
(`LOGTO_OWNER_EMAIL`/`LOGTO_OWNER_PASSWORD`), grants that user every `scenario:<slug>`
organization role, syncs the result into the local `entitlements` table, and creates
ui-react's SPA application (the OIDC client its "Log in with Logto" button needs) —
so signing in through Logto's hosted page with that email lands on every scenario,
the same way the `ADMIN_API_KEY` bypass already does.

Exercised end-to-end against a live Logto 1.42 tenant (not just written to spec, unlike
`sync_logto_entitlements.py`'s own equivalent caveat) — but re-verify against your
Logto version's API reference if you're running a materially different version.

Run manually (`make provision-owner-user`), any time after the one-time Console
bootstrap (owner sign-up + Organizations enabled + M2M app registered — see the root
README's "First-time Logto setup") — including again after a `make reset-all`, once
that bootstrap has been redone and `LOGTO_M2M_APP_ID`/`SECRET` are back in `.env`.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

from pathlib import Path

import httpx
from ai_circus_shared.scenario_schema import load_all
from sqlalchemy.orm import Session

from platform_registry import get_env_config, get_logger
from platform_registry.core.db import init_engine
from platform_registry.tools.sync_logto_entitlements import fetch_m2m_token, sync_entitlements

logger = get_logger(__name__)

OWNER_ORG_NAME = "owner"
UI_APPLICATION_NAME = "ai-circus-ui-react"
# ui-react's two local dev origins — the Traefik-served build and the Vite dev
# server (see ui-react/src/config.ts's own fallback defaults).
UI_REDIRECT_ORIGINS = ["http://react.localhost", "http://localhost:5173"]


def ensure_email_sign_in(client: httpx.Client) -> None:
    """Switch the tenant's Sign-in Experience to accept email (not Logto's default
    username) as the sign-in identifier — users created here only ever get a
    `primaryEmail`, never a `username`, so they can't sign in until this is set.
    Deliberately leaves `signUp` alone: self-registration isn't this tool's concern,
    and switching its identifier to email requires enabling verification codes,
    which needs an email connector this tenant may not have configured.
    """
    current = client.get("/api/sign-in-exp").json()
    if any(m.get("identifier") == "email" for m in current["signIn"]["methods"]):
        return
    client.patch(
        "/api/sign-in-exp",
        json={
            "signIn": {
                "methods": [
                    {"identifier": "email", "password": True, "verificationCode": False, "isPasswordPrimary": True}
                ]
            }
        },
    ).raise_for_status()
    logger.info("Switched Sign-in Experience to accept email as the sign-in identifier")


def ensure_api_resource(client: httpx.Client, indicator: str) -> None:
    """Register `indicator` as an API resource if it isn't one already."""
    resources = client.get("/api/resources").json()
    if any(r["indicator"] == indicator for r in resources):
        return
    client.post(
        "/api/resources", json={"name": "ai-circus-framework backend", "indicator": indicator}
    ).raise_for_status()
    logger.info("Registered API resource {}", indicator)


def ensure_organization_roles(client: httpx.Client, role_names: set[str]) -> None:
    """Create any `scenario:<slug>` organization role that doesn't already exist."""
    existing = {r["name"] for r in client.get("/api/organization-roles").json()}
    for name in role_names - existing:
        client.post("/api/organization-roles", json={"name": name, "type": "User"}).raise_for_status()
        logger.info("Created organization role {}", name)


def ensure_spa_application(client: httpx.Client, name: str, redirect_origins: list[str]) -> str:
    """Return the id of the SPA application named `name` (ui-react's OIDC client),
    creating it if missing, with a redirect/post-logout URI per origin in
    `redirect_origins` (each origin's hosted-page callback + its own root).
    """
    applications = client.get("/api/applications").json()
    for app in applications:
        if app["name"] == name:
            return app["id"]
    response = client.post(
        "/api/applications",
        json={
            "name": name,
            "type": "SPA",
            "oidcClientMetadata": {
                "redirectUris": [f"{origin}/callback" for origin in redirect_origins],
                "postLogoutRedirectUris": redirect_origins,
            },
        },
    )
    response.raise_for_status()
    app_id = response.json()["id"]
    logger.info("Created SPA application {} ({})", name, app_id)
    return app_id


def ensure_organization(client: httpx.Client, name: str) -> str:
    """Return the id of the Organization named `name`, creating it if missing."""
    organizations = client.get("/api/organizations").json()
    for org in organizations:
        if org["name"] == name:
            return org["id"]
    response = client.post("/api/organizations", json={"name": name})
    response.raise_for_status()
    org_id = response.json()["id"]
    logger.info("Created organization {} ({})", name, org_id)
    return org_id


def ensure_user(client: httpx.Client, email: str, password: str) -> str:
    """Return the id of the Logto user with primaryEmail `email`, creating it if missing."""
    matches = client.get("/api/users", params={"search": email}).json()
    for user in matches:
        if user.get("primaryEmail") == email:
            return user["id"]
    response = client.post("/api/users", json={"primaryEmail": email, "password": password})
    response.raise_for_status()
    user_id = response.json()["id"]
    logger.info("Created user {} ({})", email, user_id)
    return user_id


def ensure_org_membership(client: httpx.Client, org_id: str, user_id: str) -> None:
    """Add `user_id` to `org_id` if not already a member (idempotent no-op otherwise)."""
    members = client.get(f"/api/organizations/{org_id}/users").json()
    if any(m["id"] == user_id for m in members):
        return
    client.post(f"/api/organizations/{org_id}/users", json={"userIds": [user_id]}).raise_for_status()
    logger.info("Added user {} to organization {}", user_id, org_id)


def assign_scenario_roles(client: httpx.Client, org_id: str, user_id: str, role_names: set[str]) -> None:
    """Assign every `scenario:<slug>` organization role in `role_names` to `user_id` in `org_id`."""
    roles_by_name = {r["name"]: r["id"] for r in client.get("/api/organization-roles").json()}
    current = {r["name"] for r in client.get(f"/api/organizations/{org_id}/users/{user_id}/roles").json()}
    missing = role_names - current
    if not missing:
        return
    role_ids = [roles_by_name[name] for name in missing if name in roles_by_name]
    client.post(
        f"/api/organizations/{org_id}/users/{user_id}/roles",
        json={"organizationRoleIds": role_ids},
    ).raise_for_status()
    logger.info("Assigned {} scenario role(s) to user {} in organization {}", len(role_ids), user_id, org_id)


def main() -> None:
    """CLI entry point: provision the owner Organization/user and sync its entitlements."""
    config = get_env_config()
    if not (config.LOGTO_ENDPOINT and config.LOGTO_M2M_APP_ID and config.LOGTO_M2M_APP_SECRET):
        logger.error("LOGTO_ENDPOINT / LOGTO_M2M_APP_ID / LOGTO_M2M_APP_SECRET must be set to run this tool.")
        raise SystemExit(1)
    if not (config.LOGTO_OWNER_EMAIL and config.LOGTO_OWNER_PASSWORD):
        logger.error("LOGTO_OWNER_EMAIL / LOGTO_OWNER_PASSWORD must be set to run this tool.")
        raise SystemExit(1)
    if not config.LOGTO_API_RESOURCE_INDICATOR:
        logger.error("LOGTO_API_RESOURCE_INDICATOR must be set to run this tool.")
        raise SystemExit(1)

    role_names = {d.role_required for d in load_all(Path(config.SCENARIOS_DIR))}

    token = fetch_m2m_token(
        config.LOGTO_ENDPOINT, config.LOGTO_M2M_APP_ID, config.LOGTO_M2M_APP_SECRET.get_secret_value()
    )
    engine = init_engine(config)
    with (
        Session(engine) as session,
        httpx.Client(base_url=config.LOGTO_ENDPOINT, headers={"Authorization": f"Bearer {token}"}) as client,
    ):
        ensure_email_sign_in(client)
        ensure_api_resource(client, config.LOGTO_API_RESOURCE_INDICATOR)
        ensure_organization_roles(client, role_names)
        ui_app_id = ensure_spa_application(client, UI_APPLICATION_NAME, UI_REDIRECT_ORIGINS)
        org_id = ensure_organization(client, OWNER_ORG_NAME)
        user_id = ensure_user(client, config.LOGTO_OWNER_EMAIL, config.LOGTO_OWNER_PASSWORD.get_secret_value())
        ensure_org_membership(client, org_id, user_id)
        assign_scenario_roles(client, org_id, user_id, role_names)

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
        "ui-react SPA application id: {} — set UI_REACT_LOGTO_APP_ID to this in .env, then "
        "'docker compose up -d --build ui-react' to bake it into the login screen's "
        "'Log in with Logto' button.",
        ui_app_id,
    )


if __name__ == "__main__":
    main()
