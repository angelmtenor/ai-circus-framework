"""
- Title:    Caller identity resolution (Logto token validation + entitlement check)
- Author:   ai-circus-framework contributors

Entitlement enforcement happens here, at the API — not just in whichever UI called
us — per the platform's core design requirement (see root AGENTS.md). AUTH_DISABLED
is a dev-only bypass for iterating before Logto is configured; it must stay "false"
anywhere beyond local development.

Duplicated (with prediction/assistant's core/identity.py) rather than shared: each
service's `get_env_config()` returns its own generated EnvConfig type, so there's no
common config type to hand to a shared helper without deeper refactoring. This is now
the third copy — worth consolidating into ai_circus_shared as a follow-up.
"""

from __future__ import annotations

from ai_circus_shared.auth import Identity, TokenValidationError, validate_token
from ai_circus_shared.entitlements import EntitlementDeniedError, PlatformRegistryClient
from fastapi import Header, HTTPException

from rag_agent import get_env_config
from rag_agent.core.logger import get_logger

logger = get_logger(__name__)


def resolve_identity(authorization: str | None = Header(default=None)) -> Identity:
    """FastAPI dependency: resolve and validate the caller's identity from the bearer token.

    Raises:
        HTTPException: 401 if the token is missing/invalid, 403 if the tenant isn't
            entitled to this scenario.
    """
    config = get_env_config()

    if config.AUTH_DISABLED.lower() == "true":
        logger.warning("AUTH_DISABLED=true — using fixed dev identity, no token validation performed.")
        dev_role = f"scenario:{config.SCENARIO_SLUG}"
        identity = Identity(subject="dev", org_id=config.DEV_ORG_ID, roles=frozenset({dev_role}))
    else:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")
        if not (config.LOGTO_ISSUER and config.LOGTO_API_RESOURCE_INDICATOR and config.LOGTO_JWKS_URL):
            raise RuntimeError(
                "LOGTO_ISSUER/LOGTO_API_RESOURCE_INDICATOR/LOGTO_JWKS_URL must be set unless AUTH_DISABLED=true."
            )
        token = authorization.removeprefix("Bearer ")
        try:
            identity = validate_token(
                token,
                issuer=config.LOGTO_ISSUER,
                audience=config.LOGTO_API_RESOURCE_INDICATOR,
                jwks_url=config.LOGTO_JWKS_URL,
            )
        except TokenValidationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    if identity.org_id is None:
        raise HTTPException(status_code=401, detail="Token has no organization (tenant) claim.")

    client = PlatformRegistryClient(base_url=config.PLATFORM_REGISTRY_URL)
    try:
        client.check_entitlement(org_id=identity.org_id, scenario_slug=config.SCENARIO_SLUG)
    except EntitlementDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return identity
