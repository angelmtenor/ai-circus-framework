"""
app.py
------

Entry point for data-api: the governed door of every `kind: data_product` scenario (records,
search, metrics, governed SQL, datasets, pipeline events, simulator controls — see api.py and
docs/data-platform/).

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
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from starlette.concurrency import run_in_threadpool

from data_api import get_env_config
from data_api.api import router
from data_api.core.logger import configure_logger, get_logger
from data_api.core.runtime import build_runtime

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Connect Postgres/SeaweedFS (waiting for them), Kafka lazily; start the events relay."""
    runtime = await run_in_threadpool(build_runtime, get_env_config())
    app.state.runtime = runtime
    try:
        yield
    finally:
        runtime.relay.stop()
        runtime.simulation.stop_all()


_DOCS_ENABLED = os.getenv("APP_ENVIRONMENT", "local") in {"local", "docker"}

app = FastAPI(
    title="data-api",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)
app.include_router(router)
configure_metrics(app)


@app.exception_handler(OperationalError)
def _warehouse_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
    """A statement timeout or a database restart is a 503 with CORS headers (see prediction's
    handler for why an exception handler, not a bare 500), never an opaque 'Failed to fetch'.
    """
    logger.warning("{}: {}", request.url.path, str(exc).splitlines()[0])
    return JSONResponse(status_code=503, content={"detail": "The warehouse did not answer in time; try again."})


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
    # Guarded on middleware_stack — see data-platform-manager's app.py.
    if app.middleware_stack is None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[origin.strip() for origin in config.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    logger.success("data-api starting on port {}", config.HTTP_PORT)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
