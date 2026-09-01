"""Tests for the Logto entitlement pull-sync tool (no live Logto instance needed).

Uses httpx.MockTransport to fake the Management API responses, so these tests verify
the aggregation/upsert logic — not the exact Logto endpoint paths, which are documented
as best-effort/unverified against a live tenant in the module's own docstring.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_registry.core.models import Base, Entitlement, Scenario
from platform_registry.tools.sync_logto_entitlements import fetch_m2m_token, sync_entitlements

ORGANIZATIONS = [{"id": "org-1"}, {"id": "org-2"}]
ORG_MEMBERS = {
    "org-1": [{"id": "user-a"}, {"id": "user-b"}],
    "org-2": [{"id": "user-c"}],
}
MEMBER_ROLES = {
    "user-a": [{"name": "scenario:churn"}],
    "user-b": [{"name": "scenario:docs_rag"}, {"name": "some-other-role"}],
    "user-c": [],
}


def _fake_management_api(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/organizations":
        return httpx.Response(200, json=ORGANIZATIONS)
    for org_id, members in ORG_MEMBERS.items():
        if path == f"/api/organizations/{org_id}/users":
            return httpx.Response(200, json=members)
    for user_id, roles in MEMBER_ROLES.items():
        if path == f"/api/organizations/org-1/users/{user_id}/roles" or path.endswith(f"users/{user_id}/roles"):
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
    """An httpx.Client backed by a MockTransport standing in for Logto's Management API."""
    with httpx.Client(base_url="http://logto.localhost", transport=httpx.MockTransport(_fake_management_api)) as c:
        yield c


def test_sync_entitlements_grants_roles_present_in_logto(session: Session, mock_client: httpx.Client) -> None:
    """Each org ends up entitled to exactly the scenarios its members' roles map to."""
    result = sync_entitlements(session, mock_client)

    assert result == {"org-1": {"churn", "docs_rag"}, "org-2": set()}
    org_1_slugs = {e.scenario_slug for e in session.query(Entitlement).filter_by(org_id="org-1")}
    assert org_1_slugs == {"churn", "docs_rag"}


def test_sync_entitlements_revokes_roles_no_longer_present(session: Session, mock_client: httpx.Client) -> None:
    """A stale entitlement not reflected in Logto anymore is removed on the next sync."""
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
    """fetch_m2m_token exchanges M2M credentials for an access token via the OIDC token endpoint."""

    def fake_token_endpoint(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oidc/token"
        body = request.read().decode()
        assert "grant_type=client_credentials" in body
        assert "resource=https%3A%2F%2Fdefault.logto.app%2Fapi" in body
        return httpx.Response(200, json={"access_token": "fake-token"})

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        with httpx.Client(transport=httpx.MockTransport(fake_token_endpoint)) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", fake_post)

    token = fetch_m2m_token("http://logto.localhost", "app-id", "app-secret")

    assert token == "fake-token"  # ruff: ignore[hardcoded-password-string]
