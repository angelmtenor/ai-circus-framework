"""
- Title:    Caller identity resolution (thin wrapper around the shared implementation)
- Author:   ai-circus-framework contributors

Entitlement enforcement happens here, at the API — not just in whichever UI called
us — per the platform's core design requirement (see root AGENTS.md). The actual
logic (AUTH_DISABLED bypass, ADMIN_API_KEY bypass, Logto validation, entitlement
check) lives in `ai_circus_shared.auth.resolve_caller_identity`; this wrapper just
adapts this service's own generated `EnvConfig` into that call and translates its
plain domain exceptions into `HTTPException`s (kept out of `libs/shared` so it stays
free of a `fastapi` dependency — see the root plan's "Identity consolidation" decision).
"""

from __future__ import annotations

from ai_circus_shared.auth import AuthSettingsAdapter, Identity, TokenValidationError, resolve_caller_identity
from ai_circus_shared.entitlements import EntitlementDeniedError
from fastapi import Header, HTTPException

from rag_agent import get_env_config


def resolve_identity(scenario_slug: str, authorization: str | None = Header(default=None)) -> Identity:
    """FastAPI dependency: resolve and validate the caller's identity for `scenario_slug`.

    `scenario_slug` is injected from the route's path parameter of the same name
    (see e.g. `POST /chat/{scenario_slug}`) — FastAPI resolves path params into a
    `Depends()` sub-dependency by parameter name, regardless of which function in the
    dependency tree declares them.

    Raises:
        HTTPException: 401 if the token is missing/invalid, 403 if the tenant isn't
            entitled to this scenario.
    """
    config = get_env_config()
    settings = AuthSettingsAdapter(
        AUTH_DISABLED=config.AUTH_DISABLED,
        DEV_ORG_ID=config.DEV_ORG_ID,
        LOGTO_ISSUER=config.LOGTO_ISSUER,
        LOGTO_API_RESOURCE_INDICATOR=config.LOGTO_API_RESOURCE_INDICATOR,
        LOGTO_JWKS_URL=config.LOGTO_JWKS_URL,
        ADMIN_API_KEY=config.ADMIN_API_KEY.get_secret_value(),
        PLATFORM_REGISTRY_URL=config.PLATFORM_REGISTRY_URL,
    )
    try:
        return resolve_caller_identity(authorization=authorization, scenario_slug=scenario_slug, settings=settings)
    except TokenValidationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except EntitlementDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
