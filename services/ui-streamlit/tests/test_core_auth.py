"""Tests for the Logto OIDC login helpers and DEV_MODE identity bypass."""

from __future__ import annotations

import pytest

from ui_streamlit.core.auth import Identity, build_authorize_url, dev_identity, identity_from_claims


def test_dev_identity_builds_fixed_identity_with_no_access_token() -> None:
    """dev_identity() mirrors the backend's AUTH_DISABLED bypass: no real token."""
    identity = dev_identity("org-1", ["scenario:churn", "scenario:docs_rag"])

    assert identity == Identity(
        org_id="org-1", roles=frozenset({"scenario:churn", "scenario:docs_rag"}), access_token=None
    )


def test_build_authorize_url_points_at_the_issuer_auth_endpoint() -> None:
    """The authorize URL targets Logto's /auth endpoint and carries the client id."""
    url, state = build_authorize_url(
        issuer="http://logto.localhost/oidc",
        client_id="my-client-id",
        redirect_uri="http://app.localhost/callback",
        resource="https://api.ai-circus-framework.local",
    )

    assert url.startswith("http://logto.localhost/oidc/auth")
    assert "client_id=my-client-id" in url
    assert f"state={state}" in url
    assert len(state) > 10


def test_build_authorize_url_uses_provided_state_when_given() -> None:
    """An explicit state is preserved rather than a random one being generated."""
    _url, state = build_authorize_url(
        issuer="http://logto.localhost/oidc",
        client_id="my-client-id",
        redirect_uri="http://app.localhost/callback",
        resource="https://api.ai-circus-framework.local",
        state="fixed-state-value",
    )

    assert state == "fixed-state-value"


def test_identity_from_claims_extracts_org_and_roles() -> None:
    """identity_from_claims() reads the organization_id and roles claims."""
    claims = {"organization_id": "org-1", "roles": ["scenario:churn"]}

    identity = identity_from_claims(claims, access_token="the-token")  # ruff: ignore[hardcoded-password-func-arg]

    assert identity == Identity(org_id="org-1", roles=frozenset({"scenario:churn"}), access_token="the-token")  # ruff: ignore[hardcoded-password-func-arg]


def test_identity_from_claims_requires_an_organization_claim() -> None:
    """A token with no organization_id claim (e.g. a non-org-scoped token) is rejected."""
    with pytest.raises(ValueError, match="organization"):
        identity_from_claims({"roles": ["scenario:churn"]}, access_token="the-token")  # ruff: ignore[hardcoded-password-func-arg]
