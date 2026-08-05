"""Validate Logto-issued OIDC access tokens and extract tenant/role claims.

Every backend service (prediction, assistant, rag-agent, platform-registry, ...) uses
this to turn an `Authorization: Bearer <token>` header into an `Identity` before
checking entitlements via `entitlements.py`.

Logto setup this assumes (configure once in the Logto Admin Console):
  - An API resource is registered for the framework's backend (its identifier is
    `audience` below).
  - Organizations are enabled and used as tenants; users are added as organization
    members and assigned organization roles named `scenario:<slug>` (see
    `scenarios/*/scenario.yaml`).
  - The organization token's custom claims include `organization_id` and `roles`
    (Logto includes these by default on organization-scoped tokens; if a differently
    named claim is used, override `ROLES_CLAIM`/`ORG_CLAIM` below).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient

ROLES_CLAIM = "roles"
ORG_CLAIM = "organization_id"


@dataclass(frozen=True)
class Identity:
    """Resolved caller identity from a validated Logto access token."""

    subject: str
    org_id: str | None
    roles: frozenset[str]

    def has_role(self, role: str) -> bool:
        """Return whether the caller's token carries the given role."""
        return role in self.roles


class TokenValidationError(Exception):
    """Raised when a bearer token fails signature, issuer, audience, or expiry checks."""


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    """Cache one PyJWKClient (and its key cache) per JWKS URL."""
    return PyJWKClient(jwks_url)


def validate_token(token: str, *, issuer: str, audience: str, jwks_url: str) -> Identity:
    """Validate a Logto-issued access token and extract the caller's identity.

    Args:
        token: Raw bearer token (without the "Bearer " prefix).
        issuer: Expected Logto OIDC issuer, e.g. `https://<logto-host>/oidc`.
        audience: Expected API resource identifier registered in Logto.
        jwks_url: Logto JWKS endpoint, e.g. `https://<logto-host>/oidc/jwks`.

    Returns:
        The resolved `Identity` (subject, tenant org id, role set).

    Raises:
        TokenValidationError: If the token is malformed, expired, or fails
            signature/issuer/audience verification.
    """
    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES384"],
            issuer=issuer,
            audience=audience,
        )
    except jwt.PyJWTError as exc:
        raise TokenValidationError(str(exc)) from exc

    roles = frozenset(claims.get(ROLES_CLAIM, []) or [])
    return Identity(subject=claims["sub"], org_id=claims.get(ORG_CLAIM), roles=roles)
