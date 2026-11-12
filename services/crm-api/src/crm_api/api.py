"""
api.py
------

The example customer API's routes, following the API checklist of the MCP Lite Stack (docs/adr/0022):

- accept only a token issued for this API (issuer, signature, expiry, audience = this API);
- take the user and tenant from that token, never from the request; a request that names another
  user or tenant gets 403 and an audit line (rule R7);
- return only what the caller's role may see, with personal fields masked.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request

from crm_api.core import customers
from crm_api.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

ANALYST = "retail_analyst"
STORE_MANAGER = "retail_store_manager"
# Query parameters a caller might use to name someone else. The token decides; these never do.
IDENTITY_PARAMS = ("user", "user_id", "subject", "org", "org_id", "tenant")
MAX_LIMIT = 50


@dataclass(frozen=True)
class Caller:
    """The token's user, tenant (organization alias and id), roles and store claim."""

    subject: str
    tenant: str
    tenant_id: str
    roles: frozenset[str]
    stores: tuple[str, ...]


KeyResolver = Callable[[str], Any]


@dataclass(frozen=True)
class TokenSettings:
    """What a token must say to be accepted here, and how to find the key that signed it."""

    issuer: str
    audience: str
    key_for: KeyResolver


def _caller_from(authorization: str | None, settings: TokenSettings) -> Caller:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token.", headers={"WWW-Authenticate": "Bearer"})
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = jwt.decode(
            token,
            settings.key_for(token),
            algorithms=["RS256", "ES256", "PS256"],
            issuer=settings.issuer,
            audience=settings.audience,
            leeway=30,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        logger.info("crm-api access refused: {}", type(exc).__name__)
        raise HTTPException(401, "This API only accepts a valid token issued for it.") from exc
    organizations = claims.get("organization")
    if not isinstance(organizations, dict) or len(organizations) != 1:
        raise HTTPException(401, "The token must belong to exactly one organization.")
    tenant, details = next(iter(organizations.items()))
    stores = claims.get("stores") or []
    return Caller(
        subject=claims["sub"],
        tenant=tenant,
        tenant_id=str((details or {}).get("id", "")),
        roles=frozenset((claims.get("realm_access") or {}).get("roles") or []),
        stores=tuple(s for s in stores if isinstance(s, str)),
    )


def caller(request: Request, authorization: Annotated[str | None, Header()] = None) -> Caller:
    """The validated caller; also refuses a request that names another user or tenant (403, audited)."""
    who = _caller_from(authorization, request.app.state.token_settings)
    own = {"user": who.subject, "user_id": who.subject, "subject": who.subject}
    own |= {"org": who.tenant, "tenant": who.tenant, "org_id": who.tenant_id}
    for name in IDENTITY_PARAMS:
        value = request.query_params.get(name)
        if value is not None and value != own[name]:
            logger.warning("crm-api identity mismatch: sub={} org={} param={}", who.subject, who.tenant_id, name)
            raise HTTPException(403, "The request names a different user or tenant than its token.")
    return who


def store_scope(who: Caller) -> tuple[str, ...] | None:
    """Which stores this role may see: every store (None) for an analyst, its own for a store manager."""
    if ANALYST in who.roles:
        return None
    if STORE_MANAGER in who.roles:
        return who.stores
    raise HTTPException(403, "No role of this token may read customers.")


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness and readiness."""
    return {"status": "ok"}


@router.get("/customers")
def list_customers(
    who: Annotated[Caller, Depends(caller)],
    q: Annotated[str, Query(max_length=100)] = "",
    sort: Annotated[str, Query(pattern="^(name|lifetime_value)$")] = "name",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 10,
) -> dict[str, Any]:
    """Customers the caller may see, matching `q`, masked."""
    scope = store_scope(who)
    rows = customers.search(customers.visible_to(who.tenant, scope), q, sort, limit)
    logger.info("crm-api list: sub={} org={} returned={}", who.subject, who.tenant_id, len(rows))
    return {
        "customers": [customers.public_view(c) for c in rows],
        "visible_stores": "all" if scope is None else list(scope),
        "count": len(rows),
    }


@router.get("/customers/{customer_id}")
def get_customer(
    who: Annotated[Caller, Depends(caller)],
    customer_id: Annotated[str, Path(max_length=10)],
) -> dict[str, Any]:
    """One customer, if the caller may see it; otherwise 404 (its existence stays hidden)."""
    scope = store_scope(who)
    match = next((c for c in customers.visible_to(who.tenant, scope) if c.customer_id == customer_id), None)
    if match is None:
        raise HTTPException(404, "Customer not found.")
    logger.info("crm-api get: sub={} org={} found=1", who.subject, who.tenant_id)
    return customers.public_view(match)
