"""Tests for the Keycloak entitlement pull-sync tool (no live Keycloak instance needed).

Uses httpx.MockTransport to fake the Admin REST API responses, so these tests verify the
aggregation/upsert logic — not the exact Keycloak endpoint paths, which are documented as
best-effort/unverified against a live tenant in the module's own docstring.

The member -> per-user realm-role-mappings flow (`fetch_org_scenario_roles`) is the one
functionally testable consequence of Keycloak's Organizations resource having no org-scoped
roles endpoint (unlike Logto's `/organizations/{id}/users/{id}/roles`) — see
`test_fetch_org_scenario_roles_unions_realm_roles_across_members` below.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_registry.core.models import Base, Entitlement, Scenario
from platform_registry.tools.sync_keycloak_entitlements import (
    admin_request,
    fetch_m2m_token,
    fetch_org_scenario_roles,
    sync_entitlements,
)

REALM_BASE_URL = "http://keycloak.localhost/admin/realms/ai-circus"

ORGANIZATIONS = [{"id": "org-1", "name": "org-1"}, {"id": "org-2", "name": "org-2"}]
ORG_MEMBERS = {
    "org-1": [{"id": "user-a"}, {"id": "user-b"}],
    "org-2": [{"id": "user-c"}],
}
MEMBER_REALM_ROLES = {
    "user-a": [{"id": "r1", "name": "scenario:churn"}],
    "user-b": [{"id": "r2", "name": "scenario:docs_rag"}, {"id": "r3", "name": "some-other-role"}],
    "user-c": [],
}


def _fake_admin_api(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/admin/realms/ai-circus/organizations":
        return httpx.Response(200, json=ORGANIZATIONS)
    for org_id, members in ORG_MEMBERS.items():
        if path == f"/admin/realms/ai-circus/organizations/{org_id}/members":
            return httpx.Response(200, json=members)
    for user_id, roles in MEMBER_REALM_ROLES.items():
        if path == f"/admin/realms/ai-circus/users/{user_id}/role-mappings/realm":
            return httpx.Response(200, json=roles)
    raise AssertionError(f"Unexpected request: {path}")


@pytest.fixture
def session() -> Session:
    """An in-memory SQLite session pre-seeded with the two repo scenarios."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Scenario(
                slug="churn",
                kind="tabular_ml",
                title="Churn",
                description="d",
                icon="📉",
                role_required="scenario:churn",
                industry="banking_finance",
            ),
            Scenario(
                slug="docs_rag",
                kind="conversational_rag",
                title="RAG",
                description="d",
                icon="💬",
                role_required="scenario:docs_rag",
                industry="general",
            ),
        ])
        session.commit()
        yield session


@pytest.fixture
def mock_client() -> httpx.Client:
    """An httpx.Client backed by a MockTransport standing in for Keycloak's Admin REST API."""
    with httpx.Client(base_url=REALM_BASE_URL, transport=httpx.MockTransport(_fake_admin_api)) as c:
        yield c


def test_fetch_org_scenario_roles_unions_realm_roles_across_members(mock_client: httpx.Client) -> None:
    """Resolves an org's scenario roles by listing members, then unioning each member's
    individual realm-role mappings — the behavior Keycloak's missing org-scoped roles
    endpoint forces (see this module's docstring).
    """
    assert fetch_org_scenario_roles(mock_client, "org-1") == {"scenario:churn", "scenario:docs_rag"}
    assert fetch_org_scenario_roles(mock_client, "org-2") == set()


def test_sync_entitlements_grants_roles_present_in_keycloak(session: Session, mock_client: httpx.Client) -> None:
    """Each org ends up entitled to exactly the scenarios its members' realm roles map to."""
    result = sync_entitlements(session, mock_client)

    assert result == {"org-1": {"churn", "docs_rag"}, "org-2": set()}
    org_1_slugs = {e.scenario_slug for e in session.query(Entitlement).filter_by(org_id="org-1")}
    assert org_1_slugs == {"churn", "docs_rag"}


def test_sync_entitlements_revokes_roles_no_longer_present(session: Session, mock_client: httpx.Client) -> None:
    """A stale entitlement not reflected in Keycloak anymore is removed on the next sync."""
    session.add(Entitlement(org_id="org-2", scenario_slug="churn"))
    session.commit()

    sync_entitlements(session, mock_client)

    assert session.query(Entitlement).filter_by(org_id="org-2").count() == 0


def test_sync_entitlements_is_idempotent(session: Session, mock_client: httpx.Client) -> None:
    """Running the sync twice doesn't duplicate entitlement rows."""
    sync_entitlements(session, mock_client)
    sync_entitlements(session, mock_client)

    assert session.query(Entitlement).filter_by(org_id="org-1").count() == 2


def test_fetch_m2m_token_posts_client_credentials_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_m2m_token exchanges M2M credentials for an access token via the realm's OIDC
    token endpoint, with no `resource` param — Keycloak, unlike Logto, has no reserved
    resource indicator for its own Admin API.
    """

    def fake_token_endpoint(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/realms/ai-circus/protocol/openid-connect/token"
        body = request.read().decode()
        assert "grant_type=client_credentials" in body
        assert "resource" not in body
        return httpx.Response(200, json={"access_token": "fake-token"})

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        with httpx.Client(transport=httpx.MockTransport(fake_token_endpoint)) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", fake_post)

    token = fetch_m2m_token("http://keycloak.localhost", "ai-circus", "client-id", "client-secret")

    assert token == "fake-token"  # ruff: ignore[hardcoded-password-string]


def test_admin_request_raises_actionable_error_on_403(mock_client: httpx.Client) -> None:
    """A 403 from the Admin API is translated into a message pointing at the one-time
    service-account privilege bootstrap, rather than a generic HTTPStatusError.
    """

    def forbidden(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "not_authorized"})

    with httpx.Client(base_url=REALM_BASE_URL, transport=httpx.MockTransport(forbidden)) as forbidden_client:
        with pytest.raises(RuntimeError, match="manage-users"):
            admin_request(forbidden_client, "GET", "/organizations")
