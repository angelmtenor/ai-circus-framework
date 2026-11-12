"""
app.py
------

Entry point for crm-api: the example customer API behind the crm-mcp MCP server (docs/adr/0022).
Validates configuration, then serves the API under uvicorn with the platform's observability
contract (`/metrics`, OTLP traces when an endpoint is set, JSON logs).

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import os
import sys

import jwt
import uvicorn
from ai_circus_shared.observability import configure_metrics
from ai_circus_shared.telemetry import configure_tracing
from fastapi import FastAPI
from pydantic import ValidationError

from crm_api import get_env_config
from crm_api.api import KeyResolver, TokenSettings, router
from crm_api.core.logger import configure_logger, get_logger

logger = get_logger(__name__)
_DOCS_ENABLED = os.getenv("APP_ENVIRONMENT", "local") in {"local", "docker"}


def create_app(token_settings: TokenSettings | None = None) -> FastAPI:
    """The API; `token_settings` is set here for tests, or by `main()` from the environment."""
    app = FastAPI(
        title="crm-api",
        docs_url="/docs" if _DOCS_ENABLED else None,
        redoc_url=None,
        openapi_url="/openapi.json" if _DOCS_ENABLED else None,
    )
    app.state.token_settings = token_settings
    app.include_router(router)
    configure_metrics(app)
    configure_tracing(app, service="crm-api")
    return app


def jwks_key_resolver(jwks_url: str) -> KeyResolver:
    """The identity provider's signing keys, fetched and cached."""
    client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600)
    return lambda token: client.get_signing_key_from_jwt(token).key


def main() -> None:
    """Validate configuration, then run the API under uvicorn."""
    configure_logger()
    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)

    app = create_app(
        TokenSettings(
            issuer=config.KEYCLOAK_ISSUER,
            audience=config.API_AUDIENCE,
            key_for=jwks_key_resolver(config.KEYCLOAK_JWKS_URL),
        )
    )
    logger.success("crm-api starting on port {} (audience {})", config.HTTP_PORT, config.API_AUDIENCE)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
