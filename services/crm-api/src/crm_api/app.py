"""
app.py
------

Entry point for crm-api: the example customer API behind the crm-mcp MCP server (docs/adr/0022).
Validates configuration, then serves the API under uvicorn with the platform's observability
contract (`/metrics`, OTLP traces when an endpoint is set, JSON logs).

At Level 3 (docs/adr/0025) the API is served on TLS_PORT with mutual TLS only: a client must present a
certificate from the one authority it shares with its MCP server. HTTP_PORT then serves `/healthz` and
`/metrics` only, for probes and the platform's scraper, which hold no client certificate.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import asyncio
import os
import ssl
import sys

import jwt
import uvicorn
from ai_circus_shared.observability import configure_metrics
from ai_circus_shared.telemetry import configure_tracing
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from crm_api import get_env_config
from crm_api.api import KeyResolver, TokenSettings, router
from crm_api.core.dpop import DpopVerifier
from crm_api.core.introspection import Introspector
from crm_api.core.logger import configure_logger, get_logger
from crm_api.data_model import EnvConfig

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


def health_app() -> Starlette:
    """Level 3's plain listener: `/healthz` and `/metrics` only, never the API."""

    def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    def metrics(_: Request) -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    return Starlette(routes=[Route("/healthz", healthz), Route("/metrics", metrics)])


LEVEL3_SETTINGS = (
    "KEYCLOAK_INTROSPECTION_URL",
    "API_CLIENT_SECRET",
    "TLS_CERT_FILE",
    "TLS_KEY_FILE",
    "TLS_CLIENT_CA_FILE",
)


def token_settings(config: EnvConfig) -> TokenSettings:
    """What a token must say here; at level 3 also the live check and the proof verifier."""
    level = int(config.SECURITY_LEVEL or "2")
    introspector = None
    if level >= 3:
        missing = [name for name in LEVEL3_SETTINGS if not getattr(config, name)]
        if missing:
            raise ValueError(f"SECURITY_LEVEL 3 needs {', '.join(missing)}")
        assert config.KEYCLOAK_INTROSPECTION_URL is not None and config.API_CLIENT_SECRET is not None
        introspector = Introspector(
            url=config.KEYCLOAK_INTROSPECTION_URL,
            client_id=config.API_AUDIENCE,
            client_secret=config.API_CLIENT_SECRET.get_secret_value(),
        )
    return TokenSettings(
        issuer=config.KEYCLOAK_ISSUER,
        audience=config.API_AUDIENCE,
        key_for=jwks_key_resolver(config.KEYCLOAK_JWKS_URL),
        level=level,
        introspector=introspector,
        dpop=DpopVerifier() if level >= 3 else None,
    )


def level3_servers(app: FastAPI, config: EnvConfig) -> list[uvicorn.Server]:
    """The API over mutual TLS (only the shared authority's client certificates) and the plain health listener."""
    log_level = config.LOG_LEVEL.lower()
    plain = uvicorn.Config(health_app(), host="0.0.0.0", port=int(config.HTTP_PORT), log_level=log_level)  # ruff: ignore[hardcoded-bind-all-interfaces]
    mutual_tls = uvicorn.Config(
        app,
        host="0.0.0.0",  # ruff: ignore[hardcoded-bind-all-interfaces]
        port=int(config.TLS_PORT or "8443"),
        log_level=log_level,
        ssl_certfile=config.TLS_CERT_FILE,
        ssl_keyfile=config.TLS_KEY_FILE,
        ssl_ca_certs=config.TLS_CLIENT_CA_FILE,
        ssl_cert_reqs=ssl.CERT_REQUIRED,
    )
    return [uvicorn.Server(plain), uvicorn.Server(mutual_tls)]


async def serve_together(servers: list[uvicorn.Server]) -> None:
    """Run every server; when one stops (a signal, a failure), stop the others too."""
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for server in servers:
        server.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)


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

    try:
        settings = token_settings(config)
    except ValueError as e:
        logger.error("Configuration error: {}", e)
        sys.exit(1)
    app = create_app(settings)
    if settings.level >= 3:
        logger.success(
            "crm-api starting at level 3: mutual TLS on port {}, health and metrics on {} (audience {})",
            config.TLS_PORT,
            config.HTTP_PORT,
            config.API_AUDIENCE,
        )
        asyncio.run(serve_together(level3_servers(app, config)))
        return
    logger.success("crm-api starting on port {} (audience {})", config.HTTP_PORT, config.API_AUDIENCE)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
