"""Validate Keycloak-issued OIDC access tokens and extract tenant/role claims.

Every backend service (prediction, assistant, rag-agent, platform-registry, ...) uses
this to turn an `Authorization: Bearer <token>` header into an `Identity` before
checking entitlements via `entitlements.py`.

Keycloak setup this assumes (see `infra/keycloak/realm-export.json`):
  - A client scope carrying an Audience protocol mapper is registered for the
    framework's backend (its identifier is `audience` below).
  - The Organizations feature is enabled (realm's `organizationsEnabled: true`,
    Keycloak >= 26) and used as tenants; users are added as organization members.
    Keycloak has no org-scoped role endpoint, so `scenario:<slug>` entitlement roles
    (see `scenarios/*/scenario.yaml`) are plain realm roles assigned directly to the
    user, not organization roles.
  - The access token's claims include `organization` (from the built-in
    "Organization Membership" mapper, requires the `organization` scope to be
    requested explicitly at sign-in — see `_extract_org_id`) and `realm_access.roles`
    (Keycloak's default realm-roles claim shape).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import jwt
from jwt import PyJWKClient

from ai_circus_shared.entitlements import PlatformRegistryClient

REALM_ACCESS_CLAIM = "realm_access"
ORGANIZATION_CLAIM = "organization"


def _extract_org_id(claims: dict) -> str | None:
    """Read the tenant org id out of Keycloak's `organization` claim.

    The built-in "Organization Membership" protocol mapper emits
    `{"organization": {"<org-alias>": {"id": "<org-id>", "groups": [...]}}}` — keyed
    by org alias, not id (see https://github.com/keycloak/keycloak/issues/39402 on
    the alias-keying and the "must request the `organization` scope explicitly, or
    the whole claim disappears" gotcha). This platform is one-org-per-user, so take
    the first (only) entry's id.
    """
    organizations = claims.get(ORGANIZATION_CLAIM) or {}
    if not organizations:
        return None
    return next(iter(organizations.values())).get("id")

# The tenant every scenario auto-grants access to at seed time (see
# platform-registry/core/seed.py) — not a bypass of entitlement checking, just a
# real, auditable, self-maintaining entitlement row for the admin credential below.
ADMIN_ORG_ID = "admin"

# A second, narrower demo tenant — auto-granted (at seed time, see
# platform-registry/core/seed.py's ENGINEERING_DEMO_SCENARIOS) only the engineering
# tabular_ml scenarios (mpm, electric_motor, energy_building), not every scenario.
# Same bypass mechanism as ADMIN_ORG_ID (an exact ENGINEERING_DEMO_API_KEY bearer
# match), just scoped by which scenarios are seeded for this org id.
ENGINEERING_DEMO_ORG_ID = "engineering-demo"


@dataclass(frozen=True)
class Identity:
    """Resolved caller identity from a validated Keycloak access token."""

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
    """Validate a Keycloak-issued access token and extract the caller's identity.

    Args:
        token: Raw bearer token (without the "Bearer " prefix).
        issuer: Expected Keycloak realm issuer, e.g. `https://<host>/realms/<realm>`.
        audience: Expected audience string registered via the Audience client-scope mapper.
        jwks_url: Keycloak realm JWKS endpoint, e.g. `https://<host>/realms/<realm>/protocol/openid-connect/certs`.

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
            # PyJWT only verifies exp/iat *if present* in the token — require them
            # outright so a token that simply omits `exp` can't skip expiry checking.
            options={"require": ["exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenValidationError(str(exc)) from exc

    roles = frozenset(claims.get(REALM_ACCESS_CLAIM, {}).get("roles", []) or [])
    return Identity(subject=claims["sub"], org_id=_extract_org_id(claims), roles=roles)


class AuthSettings(Protocol):
    """Structural shape `resolve_caller_identity` needs from a service's `EnvConfig`.

    Every service's `get_env_config()` already returns an object with these exact
    attribute names (each is its own generated pydantic-settings type — there's no
    common base class to share — so this is matched by duck typing, not inheritance).
    """

    AUTH_DISABLED: str
    DEV_ORG_ID: str
    KEYCLOAK_ISSUER: str | None
    KEYCLOAK_AUDIENCE: str | None
    KEYCLOAK_JWKS_URL: str | None
    ADMIN_API_KEY: str | None
    ENGINEERING_DEMO_API_KEY: str | None
    PLATFORM_REGISTRY_URL: str


@dataclass
class AuthSettingsAdapter:
    """Concrete `AuthSettings` a service builds from its own generated `EnvConfig`.

    A plain dataclass (not `types.SimpleNamespace`) so static type checkers can
    actually verify it satisfies the `AuthSettings` Protocol — `SimpleNamespace`'s
    dynamic `__getattr__` defeats that check. Each service's `core/identity.py`
    constructs one of these, unwrapping `ADMIN_API_KEY` from its own `SecretStr`
    field in the process (every service types real credentials as `SecretStr`).
    Deliberately mutable (not `frozen=True`): `AuthSettings`' attributes are
    read-write by default, and pyrefly checks a Protocol's read-write attributes
    against a frozen dataclass's read-only ones as an incompatible override.
    """

    AUTH_DISABLED: str
    DEV_ORG_ID: str
    KEYCLOAK_ISSUER: str | None
    KEYCLOAK_AUDIENCE: str | None
    KEYCLOAK_JWKS_URL: str | None
    ADMIN_API_KEY: str | None
    ENGINEERING_DEMO_API_KEY: str | None
    PLATFORM_REGISTRY_URL: str


def is_admin_bearer_token(authorization: str | None, admin_api_key: str) -> bool:
    """Constant-time check that `authorization` is exactly `Bearer <admin_api_key>`."""
    if not authorization or not authorization.startswith("Bearer "):
        return False
    return secrets.compare_digest(authorization.removeprefix("Bearer "), admin_api_key)


def resolve_org_identity(*, authorization: str | None, settings: AuthSettings) -> Identity:
    """Resolve the caller's identity WITHOUT enforcing any scenario entitlement.

    For platform-registry's own `/entitlements/{org_id}` reads: those endpoints ARE
    the entitlement check, so routing them through `resolve_caller_identity` would
    have platform-registry call back into its own API via `check_entitlement` — this
    covers the same four resolution paths (dev bypass / admin key / engineering-demo
    key / real Keycloak token) without that trailing call. Deliberately NOT a
    refactor of `resolve_caller_identity` into a shared helper — keeping the two
    independent avoids any risk of changing that function's already-tested behavior
    for prediction/assistant/rag-agent.

    Raises:
        TokenValidationError: No/malformed token, or a token with no org claim.
        RuntimeError: AUTH_DISABLED is false but Keycloak isn't configured.
    """
    if settings.AUTH_DISABLED.lower() == "true":
        identity = Identity(subject="dev", org_id=settings.DEV_ORG_ID, roles=frozenset())
    elif settings.ADMIN_API_KEY and is_admin_bearer_token(authorization, settings.ADMIN_API_KEY):
        identity = Identity(subject="admin", org_id=ADMIN_ORG_ID, roles=frozenset())
    elif settings.ENGINEERING_DEMO_API_KEY and is_admin_bearer_token(authorization, settings.ENGINEERING_DEMO_API_KEY):
        identity = Identity(subject="engineering-demo", org_id=ENGINEERING_DEMO_ORG_ID, roles=frozenset())
    else:
        if not authorization or not authorization.startswith("Bearer "):
            raise TokenValidationError("Missing or malformed Authorization header.")
        if not (settings.KEYCLOAK_ISSUER and settings.KEYCLOAK_AUDIENCE and settings.KEYCLOAK_JWKS_URL):
            raise RuntimeError(
                "KEYCLOAK_ISSUER/KEYCLOAK_AUDIENCE/KEYCLOAK_JWKS_URL must be set "
                "unless AUTH_DISABLED=true or a matching ADMIN_API_KEY was supplied."
            )
        token = authorization.removeprefix("Bearer ")
        identity = validate_token(
            token,
            issuer=settings.KEYCLOAK_ISSUER,
            audience=settings.KEYCLOAK_AUDIENCE,
            jwks_url=settings.KEYCLOAK_JWKS_URL,
        )

    if identity.org_id is None:
        raise TokenValidationError("Token has no organization (tenant) claim.")
    return identity


def resolve_caller_identity(*, authorization: str | None, scenario_slug: str, settings: AuthSettings) -> Identity:
    """Resolve the caller's identity, then enforce the scenario entitlement.

    Four ways to resolve an identity, tried in order: (1) `AUTH_DISABLED=true` dev
    bypass — a fixed identity, no token needed; (2) an exact `ADMIN_API_KEY` bearer
    match — resolves to the `ADMIN_ORG_ID` tenant; (3) an exact
    `ENGINEERING_DEMO_API_KEY` bearer match — resolves to the narrower
    `ENGINEERING_DEMO_ORG_ID` tenant; (4) a real Keycloak access token. Every path then
    goes through the *same* `check_entitlement` call — admin/engineering-demo access
    is a real, seeded entitlement row (see `ADMIN_ORG_ID`/`ENGINEERING_DEMO_ORG_ID`),
    not a bypass of it, so the demo key only ever unlocks whatever's actually seeded
    for it.

    Raises:
        TokenValidationError: No/malformed token, or a token with no org claim.
        EntitlementDeniedError: The resolved tenant isn't entitled to this scenario.
        RuntimeError: AUTH_DISABLED is false but Keycloak isn't configured (server
            misconfiguration, not a caller-facing auth failure).
    """
    if settings.AUTH_DISABLED.lower() == "true":
        dev_role = f"scenario:{scenario_slug}"
        identity = Identity(subject="dev", org_id=settings.DEV_ORG_ID, roles=frozenset({dev_role}))
    elif settings.ADMIN_API_KEY and is_admin_bearer_token(authorization, settings.ADMIN_API_KEY):
        admin_role = f"scenario:{scenario_slug}"
        identity = Identity(subject="admin", org_id=ADMIN_ORG_ID, roles=frozenset({admin_role}))
    elif settings.ENGINEERING_DEMO_API_KEY and is_admin_bearer_token(authorization, settings.ENGINEERING_DEMO_API_KEY):
        demo_role = f"scenario:{scenario_slug}"
        identity = Identity(subject="engineering-demo", org_id=ENGINEERING_DEMO_ORG_ID, roles=frozenset({demo_role}))
    else:
        if not authorization or not authorization.startswith("Bearer "):
            raise TokenValidationError("Missing or malformed Authorization header.")
        if not (settings.KEYCLOAK_ISSUER and settings.KEYCLOAK_AUDIENCE and settings.KEYCLOAK_JWKS_URL):
            raise RuntimeError(
                "KEYCLOAK_ISSUER/KEYCLOAK_AUDIENCE/KEYCLOAK_JWKS_URL must be set "
                "unless AUTH_DISABLED=true or a matching ADMIN_API_KEY was supplied."
            )
        token = authorization.removeprefix("Bearer ")
        identity = validate_token(
            token,
            issuer=settings.KEYCLOAK_ISSUER,
            audience=settings.KEYCLOAK_AUDIENCE,
            jwks_url=settings.KEYCLOAK_JWKS_URL,
        )

    if identity.org_id is None:
        raise TokenValidationError("Token has no organization (tenant) claim.")

    client = PlatformRegistryClient(base_url=settings.PLATFORM_REGISTRY_URL)
    client.check_entitlement(org_id=identity.org_id, scenario_slug=scenario_slug)
    return identity
