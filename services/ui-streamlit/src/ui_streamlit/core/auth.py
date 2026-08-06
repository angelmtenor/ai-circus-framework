"""
- Title:    Logto OIDC login (+ DEV_MODE bypass)
- Author:   ai-circus-framework contributors

Streamlit has no first-class OAuth redirect handling — this implements a standard
Authorization Code flow by hand: `build_authorize_url()` sends the browser to Logto's
hosted, brand-customized sign-in page; the redirect back carries `?code=...` in
`st.query_params`, which `exchange_code()` trades for tokens via Authlib.

Unverified against a live, browser-configured Logto tenant (this repo was built
without interactive browser access) — the OIDC mechanics follow the standard
Authorization Code + PKCE flow, but double-check against your Logto app's exact
settings before relying on this in production. DEV_MODE (a sidebar-picked fake
identity) is what this repo's own end-to-end testing actually exercised.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from ai_circus_shared.auth import ADMIN_ORG_ID
from authlib.integrations.requests_client import OAuth2Session


@dataclass(frozen=True)
class Identity:
    """The logged-in user's tenant (org) and roles, how ever they were established."""

    org_id: str
    roles: frozenset[str]
    access_token: str | None = None


def dev_identity(org_id: str, roles: list[str]) -> Identity:
    """Build a fixed dev-mode identity — mirrors the backend services' AUTH_DISABLED bypass."""
    return Identity(org_id=org_id, roles=frozenset(roles), access_token=None)


def admin_identity(admin_api_key: str) -> Identity:
    """Build an identity from the shared admin key — resolves to `ADMIN_ORG_ID` on every backend call.

    The key itself becomes the bearer token; the backend's own
    `ai_circus_shared.auth.resolve_caller_identity` does the actual matching (see its
    docstring) — this UI never needs to know the org_id in advance, but `ADMIN_ORG_ID`
    is used here too so `list_scenarios(org_id=...)` shows the right entitlements.
    """
    return Identity(org_id=ADMIN_ORG_ID, roles=frozenset(), access_token=admin_api_key)


def build_authorize_url(
    *,
    issuer: str,
    client_id: str,
    redirect_uri: str,
    resource: str,
    state: str | None = None,
) -> tuple[str, str]:
    """Build the Logto authorize URL for the Authorization Code flow.

    Returns:
        (authorize_url, state) — persist `state` (e.g. in st.session_state) and
        verify it matches on callback to guard against CSRF.
    """
    state = state or secrets.token_urlsafe(16)
    session = OAuth2Session(client_id, scope="openid profile offline_access")
    url, _ = session.create_authorization_url(
        f"{issuer}/auth",
        redirect_uri=redirect_uri,
        state=state,
        resource=resource,
    )
    return str(url), state


def exchange_code(
    *,
    issuer: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> dict[str, object]:
    """Exchange an authorization code for tokens at Logto's token endpoint."""
    session = OAuth2Session(client_id, client_secret, redirect_uri=redirect_uri)
    return dict(session.fetch_token(f"{issuer}/token", code=code, grant_type="authorization_code"))


def identity_from_claims(claims: dict[str, object], access_token: str) -> Identity:
    """Build an Identity from validated token claims (see ai_circus_shared.auth.validate_token)."""
    org_id = claims.get("organization_id")
    if not isinstance(org_id, str):
        raise ValueError("Token has no organization (tenant) claim.")
    raw_roles = claims.get("roles") or []
    roles = frozenset(raw_roles) if isinstance(raw_roles, list) else frozenset()
    return Identity(org_id=org_id, roles=roles, access_token=access_token)
