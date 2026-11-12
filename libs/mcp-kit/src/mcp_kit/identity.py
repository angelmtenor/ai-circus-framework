"""
identity.py
-----------

The caller, read only from a token the identity provider signed (rule R1, docs/adr/0022). The kit
never takes a user, tenant or role from a request parameter or a tool argument.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt

ORGANIZATION_CLAIM = "organization"
REALM_ACCESS_CLAIM = "realm_access"
_STANDARD_CLAIMS = frozenset({"iss", "sub", "aud", "exp", "iat", "nbf", "jti", "azp", "typ", "sid", "scope", "acr"})


class TokenRejectedError(Exception):
    """The bearer token is missing, forged, expired, for another audience or has no tenant (→ 401)."""


@dataclass(frozen=True)
class Identity:
    """Who is calling: a pseudonymous subject, one tenant, its roles and other string claims. Read-only."""

    subject: str
    org_id: str
    org_alias: str
    roles: frozenset[str]
    attributes: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def has_role(self, role: str) -> bool:
        """Whether the token carries this realm role."""
        return role in self.roles

    def attribute(self, name: str) -> tuple[str, ...]:
        """The values of one non-standard claim (empty when the token does not carry it)."""
        return next((values for key, values in self.attributes if key == name), ())


def _organization(claims: dict[str, Any]) -> tuple[str, str]:
    """(org id, org alias) from Keycloak's `{alias: {"id": …}}` organization claim; exactly one tenant."""
    organizations = claims.get(ORGANIZATION_CLAIM)
    if not isinstance(organizations, dict) or len(organizations) != 1:
        raise TokenRejectedError("The token must belong to exactly one organization.")
    alias, details = next(iter(organizations.items()))
    org_id = details.get("id") if isinstance(details, dict) else None
    if not isinstance(org_id, str) or not org_id:
        raise TokenRejectedError("The token's organization has no id.")
    return org_id, alias


def _string_claims(claims: dict[str, Any]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Non-standard claims whose value is a string or a list of strings (bounded)."""
    found = []
    for name, value in claims.items():
        if name in _STANDARD_CLAIMS or name == ORGANIZATION_CLAIM:
            continue
        if isinstance(value, str):
            found.append((name, (value[:200],)))
        elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            found.append((name, tuple(v[:200] for v in value[:100])))
    return tuple(sorted(found))[:50]


def identity_from_claims(claims: dict[str, Any]) -> Identity:
    """The identity a validated token describes."""
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise TokenRejectedError("The token has no subject.")
    org_id, org_alias = _organization(claims)
    roles = (claims.get(REALM_ACCESS_CLAIM) or {}).get("roles") or []
    return Identity(
        subject=subject,
        org_id=org_id,
        org_alias=org_alias,
        roles=frozenset(r for r in roles if isinstance(r, str)),
        attributes=_string_claims(claims),
    )


KeyResolver = Callable[[str], Any]
"""Returns the public key that verifies a given token (a JWKS lookup in production, a fixed key in tests)."""


class TokenVerifier:
    """Checks issuer, signature, expiry and that the audience is this server (rule R3)."""

    def __init__(self, *, issuer: str, audience: str, key_for: KeyResolver, leeway_seconds: int = 30) -> None:
        """`key_for(token)` returns the verifying key, e.g. `PyJWKClient(jwks_url).get_signing_key_from_jwt(t).key`."""
        self.issuer = issuer
        self.audience = audience
        self.key_for = key_for
        self.leeway_seconds = leeway_seconds

    @classmethod
    def from_jwks(cls, *, issuer: str, audience: str, jwks_url: str) -> TokenVerifier:
        """A verifier that fetches (and caches) the identity provider's signing keys."""
        client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600)
        return cls(issuer=issuer, audience=audience, key_for=lambda token: client.get_signing_key_from_jwt(token).key)

    def verify(self, authorization: str | None) -> Identity:
        """The identity of an `Authorization: Bearer …` header, or TokenRejectedError."""
        if not authorization or not authorization.startswith("Bearer "):
            raise TokenRejectedError("Missing bearer token.")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            claims = jwt.decode(
                token,
                self.key_for(token),
                algorithms=["RS256", "ES256", "ES384", "PS256"],
                issuer=self.issuer,
                audience=self.audience,
                leeway=self.leeway_seconds,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenRejectedError(f"Invalid token: {type(exc).__name__}.") from exc
        return identity_from_claims(claims)
