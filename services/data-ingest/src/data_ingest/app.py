"""
app.py
------

Entry point for data-ingest: the Data Platform's capture-to-bronze worker (see
core/runtime.py and docs/data-platform/). The HTTP app is internal — health, status and a
landing-scan trigger.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from ai_circus_shared.deployment_guard import enforce_safe_for_public_deployment
from ai_circus_shared.observability import configure_metrics
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from data_ingest import get_env_config
from data_ingest.api import router
from data_ingest.core.logger import configure_logger, get_logger
from data_ingest.core.runtime import build_runtime

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Bootstrap databases/tables and start the workers (waits for Postgres and SeaweedFS;
    Kafka connects lazily). Readiness stays red until this returns.
    """
    runtime = await run_in_threadpool(build_runtime, get_env_config())
    runtime.start()
    app.state.runtime = runtime
    try:
        yield
    finally:
        runtime.stop()


_DOCS_ENABLED = os.getenv("APP_ENVIRONMENT", "local") in {"local", "docker"}

app = FastAPI(
    title="data-ingest",
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
        # Internal, admin-key-only service: only the shipped demo ADMIN_API_KEY is a risk.
        enforce_safe_for_public_deployment(
            admin_api_key=config.ADMIN_API_KEY.get_secret_value(), auth_disabled="false", engineering_demo_api_key=None
        )
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)
    logger.success("data-ingest starting on port {}", config.HTTP_PORT)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
