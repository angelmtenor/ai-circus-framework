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

# The root .env.example's shipped ADMIN_API_KEY value — self-documented there as an
# intentionally public demo credential, fine for the "local"/"docker" dev profiles
# but never for a real deployment.
_DEMO_ADMIN_API_KEY = "ai-circus-2026"
_DEV_PROFILES = {"local", "docker"}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize the database, create tables, and seed scenarios on startup."""
    config = get_env_config()
    engine = init_engine(config)
    Base.metadata.create_all(engine)

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        seed_scenarios(session, Path(config.SCENARIOS_DIR))
        # "llama3" (the bundled, no-API-key Ollama model) — matches assistant/rag-agent's
        # own static LLM_MODEL default, so a fresh install behaves the same either way.
        seed_default_llm_setting(session, default_model_name="llama3")

    yield


app = FastAPI(title="platform-registry", lifespan=lifespan)
app.include_router(router)


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

    active_profile = os.getenv("APP_ENVIRONMENT", "local")
    if active_profile not in _DEV_PROFILES and config.ADMIN_API_KEY.get_secret_value() == _DEMO_ADMIN_API_KEY:
        logger.error(
            "ADMIN_API_KEY is still the shipped demo default in APP_ENVIRONMENT={!r} — "
            "rotate it before running outside local/docker dev.",
            active_profile,
        )
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
