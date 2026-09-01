"""
- Title:    Caller identity resolution (thin wrapper around the shared implementation)
- Author:   ai-circus-framework contributors

Entitlement enforcement happens here, at the API — not just in whichever UI called
us — per the platform's core design requirement (see root AGENTS.md). The actual
logic (AUTH_DISABLED bypass, ADMIN_API_KEY bypass, Keycloak validation, entitlement
check) lives in `ai_circus_shared.auth.resolve_caller_identity`; this wrapper just
adapts this service's own generated `EnvConfig` into that call and translates its
plain domain exceptions into `HTTPException`s, mirroring assistant/core/identity.py.
"""

from __future__ import annotations

from ai_circus_shared.auth import AuthSettingsAdapter, Identity, TokenValidationError, resolve_caller_identity
from ai_circus_shared.entitlements import EntitlementDeniedError
from fastapi import Header, HTTPException

from agui_voice import get_env_config


def resolve_identity(scenario_slug: str, authorization: str | None = Header(default=None)) -> Identity:
    """FastAPI dependency: resolve and validate the caller's identity for `scenario_slug`.

    Used by the plain-HTTP `POST /tts/{scenario_slug}` route, where a normal
    `Authorization` header is available. The WebSocket route can't set headers from
    the browser, so it calls `resolve_identity_from_token` directly instead — see
    `api/ws.py`.

    Raises:
        HTTPException: 401 if the token is missing/invalid, 403 if the tenant isn't
            entitled to this scenario.
    """
    try:
        return resolve_identity_from_token(scenario_slug, authorization)
    except TokenValidationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except EntitlementDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def resolve_identity_from_token(scenario_slug: str, authorization: str | None) -> Identity:
    """Same resolution as `resolve_identity`, without the FastAPI `Header`/`HTTPException`
    wrapping — for callers (the WebSocket handshake) that need the raw
    `TokenValidationError`/`EntitlementDeniedError` to decide their own close code.
    """
    config = get_env_config()
    settings = AuthSettingsAdapter(
        AUTH_DISABLED=config.AUTH_DISABLED,
        DEV_ORG_ID=config.DEV_ORG_ID,
        KEYCLOAK_ISSUER=config.KEYCLOAK_ISSUER,
        KEYCLOAK_AUDIENCE=config.KEYCLOAK_AUDIENCE,
        KEYCLOAK_JWKS_URL=config.KEYCLOAK_JWKS_URL,
        ADMIN_API_KEY=config.ADMIN_API_KEY.get_secret_value(),
        ENGINEERING_DEMO_API_KEY=(
            config.ENGINEERING_DEMO_API_KEY.get_secret_value() if config.ENGINEERING_DEMO_API_KEY else None
        ),
        PLATFORM_REGISTRY_URL=config.PLATFORM_REGISTRY_URL,
    )
    return resolve_caller_identity(authorization=authorization, scenario_slug=scenario_slug, settings=settings)
