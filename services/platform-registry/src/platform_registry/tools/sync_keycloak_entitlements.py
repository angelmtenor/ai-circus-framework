"""
sync_keycloak_entitlements.py
------------------------------

Pull-sync: mirrors Keycloak realm-role assignments into the local `entitlements`
table, so every backend service's entitlement check reads from Postgres instead of
calling Keycloak's Admin REST API on every request.

Best-effort against Keycloak's documented Admin REST API conventions (a
client-credentials M2M token from `{realm}/protocol/openid-connect/token`, then
`/admin/realms/{realm}/organizations` -> `/admin/realms/{realm}/organizations/{id}/members`
-> per member `/admin/realms/{realm}/users/{userId}/role-mappings/realm`). These paths
follow Keycloak's documented Admin REST API reference but haven't been exercised against
a live, browser-configured Keycloak tenant — verify against your Keycloak version's API
reference before depending on this in production.

Unlike Logto, Keycloak's Organizations resource exposes no org-scoped roles endpoint
(confirmed against Keycloak's Admin REST API reference and current GitHub issues) — a
member's `scenario:<slug>` entitlement lives as a plain **realm role** on that user, not
as an org-scoped role assignment. `fetch_org_scenario_roles` below resolves this by
listing an org's members first, then reading each member's realm role mappings
individually and filtering to `scenario:*`, unioning the result across members — the same
end goal as before ("what scenario roles does this org's membership carry"), just sourced
one API hop further out.

The M2M client's service-account user also has no Logto-style implicit admin scope: it
needs the `manage-users`/`manage-organizations`/`manage-realm`/`manage-clients`
realm-management client roles assigned to it once, out of band, via the realm's own
bootstrap admin credentials, before this tool can call the Admin API at all — see
`admin_request`'s 403 handling below.

Run manually (`make sync-entitlements`) or on a schedule. A Keycloak webhook/event-based
push sync (triggered on role-assignment events, instead of this pull/poll approach) is a
documented future improvement — see the root README's "Reserved for later" section.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_registry import get_env_config, get_logger
from platform_registry.core.db import init_engine
from platform_registry.core.models import Entitlement, Scenario

logger = get_logger(__name__)


def admin_request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Issue one Admin REST request, translating a 403 into an actionable error.

    A 403 here almost always means the M2M client's service-account user hasn't been
    granted the `manage-users`/`manage-organizations`/`manage-realm`/`manage-clients`
    realm-management client roles yet (see this module's docstring) — Keycloak has no
    Logto-style implicit "M2M app can call everything" scope, so that privilege bootstrap
    is a one-time, out-of-band prerequisite (via the realm's own `KC_BOOTSTRAP_ADMIN_*`
    credentials), not something this tool can grant itself.
    """
    response = client.request(method, path, **kwargs)
    if response.status_code == 403:
        raise RuntimeError(
            f"Keycloak Admin API returned 403 for {method} {path}. The M2M service-account "
            "user is most likely missing one or more realm-management client roles "
            "(manage-users / manage-organizations / manage-realm / manage-clients) — assign "
            "them once via the realm's bootstrap admin credentials, then re-run this tool."
        )
    response.raise_for_status()
    return response


def fetch_m2m_token(server_url: str, realm: str, client_id: str, client_secret: str) -> str:
    """Exchange M2M client credentials for an Admin REST API access token.

    Unlike Logto's reserved `https://default.logto.app/api` resource indicator, Keycloak's
    client_credentials grant takes no `resource` param — the token's effective scope is
    entirely determined by the client's own realm-management role assignments.
    """
    response = httpx.post(
        f"{server_url}/realms/{realm}/protocol/openid-connect/token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_org_scenario_roles(client: httpx.Client, org_id: str) -> set[str]:
    """Return the union of `scenario:*` realm-role names across every member of one org.

    Keycloak's Organizations resource has no org-scoped roles endpoint (unlike Logto's
    `/organizations/{id}/users/{id}/roles`), so this lists org membership first, then reads
    each member's realm role mappings individually and filters to `scenario:*`.
    """
    roles: set[str] = set()
    members = admin_request(client, "GET", f"/organizations/{org_id}/members").json()
    for member in members:
        member_roles = admin_request(client, "GET", f"/users/{member['id']}/role-mappings/realm").json()
        roles.update(role["name"] for role in member_roles if role["name"].startswith("scenario:"))
    return roles


def sync_entitlements(session: Session, client: httpx.Client) -> dict[str, set[str]]:
    """Upsert local entitlements to match each organization's `scenario:*` roles in Keycloak.

    Args:
        session: Open SQLAlchemy session (this function commits).
        client: An `httpx.Client` already authenticated against Keycloak's Admin REST API
            (base_url=f"{KEYCLOAK_SERVER_URL}/admin/realms/{KEYCLOAK_REALM}", Authorization
            header set).

    Returns:
        Map of `org_id` -> the set of scenario slugs it's now entitled to.
    """
    role_to_slug = {scenario.role_required: scenario.slug for scenario in session.query(Scenario).all()}
    organizations = admin_request(client, "GET", "/organizations").json()
    result: dict[str, set[str]] = {}

    for org in organizations:
        org_id = org["id"]
        role_names = fetch_org_scenario_roles(client, org_id)
        slugs = {role_to_slug[name] for name in role_names if name in role_to_slug}
        result[org_id] = slugs

        existing_stmt = select(Entitlement).where(Entitlement.org_id == org_id)
        existing = {e.scenario_slug: e for e in session.scalars(existing_stmt)}

        for slug in slugs - existing.keys():
            session.add(Entitlement(org_id=org_id, scenario_slug=slug))
        for slug in existing.keys() - slugs:
            session.delete(existing[slug])

    session.commit()
    return result


def main() -> None:
    """CLI entry point: sync every organization's entitlements from Keycloak."""
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
        result = sync_entitlements(session, client)

    for org_id, slugs in result.items():
        logger.info("org {}: {}", org_id, ", ".join(sorted(slugs)) or "(no scenarios)")


if __name__ == "__main__":
    main()
