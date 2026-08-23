"""
app.py
------

Entry point for platform-registry: owns the `platform` Postgres schema
(scenarios/entitlements) and seeds it from ../../scenarios/*.yaml on startup.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from ai_circus_shared.deployment_guard import enforce_safe_for_public_deployment
from ai_circus_shared.observability import configure_metrics
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from platform_registry import get_env_config
from platform_registry.api import router
from platform_registry.core.db import init_engine
from platform_registry.core.logger import configure_logger, get_logger
from platform_registry.core.models import Base
from platform_registry.core.seed import seed_default_llm_setting, seed_scenarios

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize the database, create tables, and seed scenarios on startup."""
    config = get_env_config()
    engine = init_engine(config)
    Base.metadata.create_all(engine)

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        seed_scenarios(session, Path(config.SCENARIOS_DIR))
        # "groq-llama" (GroqCloud's free tier, no local Ollama container needed) —
        # matches assistant/rag-agent's own static LLM_MODEL default, so a fresh
        # install behaves the same either way.
        seed_default_llm_setting(session, default_model_name="groq-llama")

    yield


# FastAPI/Swagger UI docs are only useful for local iteration — staging/production
# traffic goes through Traefik and shouldn't expose an interactive API explorer.
_DOCS_ENABLED = os.getenv("APP_ENVIRONMENT", "local") in {"local", "docker"}

app = FastAPI(
    title="platform-registry",
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
        enforce_safe_for_public_deployment(
            admin_api_key=config.ADMIN_API_KEY.get_secret_value(),
            engineering_demo_api_key=(
                config.ENGINEERING_DEMO_API_KEY.get_secret_value() if config.ENGINEERING_DEMO_API_KEY else None
            ),
            auth_disabled=config.AUTH_DISABLED,
        )
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)

    # ui-react calls this API directly from the browser (never via cookies, always a
    # Bearer token, so no CSRF risk) — still scoped to CORS_ALLOWED_ORIGINS rather than
    # "*" so a token that leaked to some other origin can't be replayed cross-origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in config.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logger.success("platform-registry starting on port {}", config.HTTP_PORT)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
