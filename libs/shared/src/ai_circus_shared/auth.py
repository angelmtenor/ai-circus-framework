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

import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import jwt
from jwt import PyJWKClient

from ai_circus_shared.entitlements import PlatformRegistryClient

ROLES_CLAIM = "roles"
ORG_CLAIM = "organization_id"

# The tenant every scenario auto-grants access to at seed time (see
# platform-registry/core/seed.py) — not a bypass of entitlement checking, just a
# real, auditable, self-maintaining entitlement row for the admin credential below.
ADMIN_ORG_ID = "admin"


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


class AuthSettings(Protocol):
    """Structural shape `resolve_caller_identity` needs from a service's `EnvConfig`.

    Every service's `get_env_config()` already returns an object with these exact
    attribute names (each is its own generated pydantic-settings type — there's no
    common base class to share — so this is matched by duck typing, not inheritance).
    """

    AUTH_DISABLED: str
    DEV_ORG_ID: str
    LOGTO_ISSUER: str | None
    LOGTO_API_RESOURCE_INDICATOR: str | None
    LOGTO_JWKS_URL: str | None
    ADMIN_API_KEY: str | None
    PLATFORM_REGISTRY_URL: str


def _is_admin_bearer_token(authorization: str | None, admin_api_key: str) -> bool:
    """Constant-time check that `authorization` is exactly `Bearer <admin_api_key>`."""
    if not authorization or not authorization.startswith("Bearer "):
        return False
    return secrets.compare_digest(authorization.removeprefix("Bearer "), admin_api_key)


def resolve_caller_identity(*, authorization: str | None, scenario_slug: str, settings: AuthSettings) -> Identity:
    """Resolve the caller's identity, then enforce the scenario entitlement.

    Three ways to resolve an identity, tried in order: (1) `AUTH_DISABLED=true` dev
    bypass — a fixed identity, no token needed; (2) an exact `ADMIN_API_KEY` bearer
    match — resolves to the `ADMIN_ORG_ID` tenant; (3) a real Logto access token.
    Every path then goes through the *same* `check_entitlement` call — admin access
    is a real, seeded entitlement row (see `ADMIN_ORG_ID`), not a bypass of it.

    Raises:
        TokenValidationError: No/malformed token, or a token with no org claim.
        EntitlementDeniedError: The resolved tenant isn't entitled to this scenario.
        RuntimeError: AUTH_DISABLED is false but Logto isn't configured (server
            misconfiguration, not a caller-facing auth failure).
    """
    if settings.AUTH_DISABLED.lower() == "true":
        dev_role = f"scenario:{scenario_slug}"
        identity = Identity(subject="dev", org_id=settings.DEV_ORG_ID, roles=frozenset({dev_role}))
    elif settings.ADMIN_API_KEY and _is_admin_bearer_token(authorization, settings.ADMIN_API_KEY):
        admin_role = f"scenario:{scenario_slug}"
        identity = Identity(subject="admin", org_id=ADMIN_ORG_ID, roles=frozenset({admin_role}))
    else:
        if not authorization or not authorization.startswith("Bearer "):
            raise TokenValidationError("Missing or malformed Authorization header.")
        if not (settings.LOGTO_ISSUER and settings.LOGTO_API_RESOURCE_INDICATOR and settings.LOGTO_JWKS_URL):
            raise RuntimeError(
                "LOGTO_ISSUER/LOGTO_API_RESOURCE_INDICATOR/LOGTO_JWKS_URL must be set "
                "unless AUTH_DISABLED=true or a matching ADMIN_API_KEY was supplied."
            )
        token = authorization.removeprefix("Bearer ")
        identity = validate_token(
            token,
            issuer=settings.LOGTO_ISSUER,
            audience=settings.LOGTO_API_RESOURCE_INDICATOR,
            jwks_url=settings.LOGTO_JWKS_URL,
        )

    if identity.org_id is None:
        raise TokenValidationError("Token has no organization (tenant) claim.")

    client = PlatformRegistryClient(base_url=settings.PLATFORM_REGISTRY_URL)
    client.check_entitlement(org_id=identity.org_id, scenario_slug=scenario_slug)
    return identity
