"""
app.py
------

Entry point for data-platform-manager: the admin-only control surface for the
platform's data layer and AI Gateway governance (pipeline job status/trigger,
gateway rate-limit report, capability roadmap). See api.py's module docstring
for its "admin tenant only" scope.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from ai_circus_shared.deployment_guard import enforce_safe_for_public_deployment
from ai_circus_shared.document_store import Base, init_engine
from ai_circus_shared.observability import configure_metrics
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from data_platform_manager import get_env_config
from data_platform_manager.api import router
from data_platform_manager.core.cache_client import init_client
from data_platform_manager.core.logger import configure_logger, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize the document-store database and cache connection on startup."""
    config = get_env_config()
    engine = init_engine(config)
    Base.metadata.create_all(engine)
    init_client(config)
    yield


_DOCS_ENABLED = os.getenv("APP_ENVIRONMENT", "local") in {"local", "docker"}

app = FastAPI(
    title="data-platform-manager",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)
app.include_router(router)
configure_metrics(app)


def main() -> None:
    """Validate configuration, then run the FastAPI app under uvicorn."""
    configure_logger()

    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)

    try:
        # This service has no AUTH_DISABLED/ENGINEERING_DEMO_API_KEY concept of its
        # own (admin-only, no Keycloak or engineering-demo bypass) — only the shipped
        # demo ADMIN_API_KEY is a real risk here, so the other two args are fixed.
        enforce_safe_for_public_deployment(
            admin_api_key=config.ADMIN_API_KEY.get_secret_value(),
            engineering_demo_api_key=None,
            auth_disabled="false",
        )
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)

    # ui-react's admin-only Settings section calls this API directly from the
    # browser with a Bearer token (never a cookie) — same CORS reasoning as
    # platform-registry's own app.py. Guarded on middleware_stack: Starlette
    # refuses to add middleware once the stack has been built, which a test
    # suite's TestClient (never real production traffic) can trigger before
    # main() ever runs by exercising the app directly — harmless to skip there.
    if app.middleware_stack is None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[origin.strip() for origin in config.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    logger.success("data-platform-manager starting on port {}", config.HTTP_PORT)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
