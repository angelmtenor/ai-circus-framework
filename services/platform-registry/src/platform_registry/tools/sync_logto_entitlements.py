"""
sync_logto_entitlements.py
---------------------------

Pull-sync: mirrors Logto Organization role assignments into the local `entitlements`
table, so every backend service's entitlement check reads from Postgres instead of
calling Logto's Management API on every request.

Best-effort against Logto's self-hosted Management API conventions (a client-credentials
M2M token requested for the reserved `https://default.logto.app/api` resource, then
`/api/organizations` -> `/api/organizations/{id}/users` ->
`/api/organizations/{id}/users/{userId}/roles`). These paths follow Logto's documented
conventions but haven't been exercised against a live, browser-configured Logto tenant —
verify against your Logto version's API reference before depending on this in production.

Run manually (`make sync-entitlements`) or on a schedule. A Logto webhook-based push sync
(triggered on role-assignment events, instead of this pull/poll approach) is a documented
future improvement — see the root README's "Reserved for later" section.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_registry import get_env_config, get_logger
from platform_registry.core.db import init_engine
from platform_registry.core.models import Entitlement, Scenario

logger = get_logger(__name__)

# Logto's reserved resource indicator for its own first-party Management API —
# the same value for self-hosted and cloud deployments.
LOGTO_MANAGEMENT_API_RESOURCE = "https://default.logto.app/api"


def fetch_m2m_token(endpoint: str, app_id: str, app_secret: str) -> str:
    """Exchange M2M app credentials for a Management API access token."""
    response = httpx.post(
        f"{endpoint}/oidc/token",
        data={"grant_type": "client_credentials", "resource": LOGTO_MANAGEMENT_API_RESOURCE, "scope": "all"},
        auth=(app_id, app_secret),
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_org_scenario_roles(client: httpx.Client, org_id: str) -> set[str]:
    """Return the union of `scenario:*` organization-role names across every member of one org."""
    roles: set[str] = set()
    members = client.get(f"/api/organizations/{org_id}/users").json()
    for member in members:
        member_roles = client.get(f"/api/organizations/{org_id}/users/{member['id']}/roles").json()
        roles.update(role["name"] for role in member_roles if role["name"].startswith("scenario:"))
    return roles


def sync_entitlements(session: Session, client: httpx.Client) -> dict[str, set[str]]:
    """Upsert local entitlements to match each organization's `scenario:*` roles in Logto.

    Args:
        session: Open SQLAlchemy session (this function commits).
        client: An `httpx.Client` already authenticated against Logto's Management API
            (base_url=LOGTO_ENDPOINT, Authorization header set).

    Returns:
        Map of `org_id` -> the set of scenario slugs it's now entitled to.
    """
    role_to_slug = {scenario.role_required: scenario.slug for scenario in session.query(Scenario).all()}
    organizations = client.get("/api/organizations").json()
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
    """CLI entry point: sync every organization's entitlements from Logto."""
    config = get_env_config()
    if not (config.LOGTO_ENDPOINT and config.LOGTO_M2M_APP_ID and config.LOGTO_M2M_APP_SECRET):
        logger.error("LOGTO_ENDPOINT / LOGTO_M2M_APP_ID / LOGTO_M2M_APP_SECRET must be set to run this tool.")
        raise SystemExit(1)

    token = fetch_m2m_token(
        config.LOGTO_ENDPOINT, config.LOGTO_M2M_APP_ID, config.LOGTO_M2M_APP_SECRET.get_secret_value()
    )
    engine = init_engine(config)
    with (
        Session(engine) as session,
        httpx.Client(base_url=config.LOGTO_ENDPOINT, headers={"Authorization": f"Bearer {token}"}) as client,
    ):
        result = sync_entitlements(session, client)

    for org_id, slugs in result.items():
        logger.info("org {}: {}", org_id, ", ".join(sorted(slugs)) or "(no scenarios)")


if __name__ == "__main__":
    main()
