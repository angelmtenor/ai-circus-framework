"""
app.py
------

Entry point for dl-inference: a long-running FastAPI service serving every
`deep_learning` scenario in SCENARIOS (empty/unset = all) — predictions, occlusion
explanations, similar cases and published samples — on onnxruntime only, shared across
every tenant (models are loaded and cached per (org, scenario) from SeaweedFS on first
request — see core/model_cache.py). Deployed separately from the core platform
(k8s/deep-learning/, `make k3s-dl-up`) so its memory is opt-in.

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
from ai_circus_shared.scenario_schema import resolve_scenarios
from ai_circus_shared.storage import ObjectStore
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from dl_inference import get_env_config
from dl_inference.api import router
from dl_inference.core.logger import configure_logger, get_logger
from dl_inference.core.model_cache import DlModelCache, ModelUnavailableError

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Resolve SCENARIOS to their definitions and connect to each one's SeaweedFS bucket."""
    config = get_env_config()
    definitions = resolve_scenarios(Path(config.SCENARIOS_DIR), config.SCENARIOS, kind="deep_learning")
    if not definitions:
        raise RuntimeError(
            f"No deep_learning scenario matched SCENARIOS={config.SCENARIOS!r} under {config.SCENARIOS_DIR!r}."
        )

    stores = {}
    for slug, definition in definitions.items():
        assert definition.deep_learning is not None  # guaranteed by kind="deep_learning" filter
        stores[slug] = ObjectStore.connect(
            bucket=definition.deep_learning.bucket,
            endpoint_url=config.OBJECT_STORE_ENDPOINT,
            access_key=config.OBJECT_STORE_ACCESS_KEY,
            secret_key=config.OBJECT_STORE_SECRET_KEY.get_secret_value(),
        )

    app.state.definitions = definitions
    app.state.model_cache = DlModelCache(
        stores, fallback_org_id=config.SHARED_MODEL_ORG_ID, threads=int(config.ORT_THREADS)
    )

    yield


# FastAPI/Swagger UI docs are only useful for local iteration — staging/production
# traffic goes through Traefik and shouldn't expose an interactive API explorer.
_DOCS_ENABLED = os.getenv("APP_ENVIRONMENT", "local") in {"local", "docker"}

app = FastAPI(
    title="dl-inference",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)
app.include_router(router)
configure_metrics(app)


@app.exception_handler(ModelUnavailableError)
def _model_unavailable_handler(request: Request, exc: ModelUnavailableError) -> JSONResponse:
    """Registered (rather than a bare try/except at each call site) so it's applied
    uniformly across every route, and — critically — an exception handler runs inside
    CORSMiddleware's wrapped app, so the response still carries CORS headers. An
    unhandled exception here would instead surface to the browser as an opaque
    "Failed to fetch" (Starlette's ServerErrorMiddleware sits *outside* CORSMiddleware,
    so its default 500 response skips CORS headers entirely).
    """
    logger.warning("{}: {}", request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


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

    logger.success("dl-inference starting on port {} for SCENARIOS={!r}", config.HTTP_PORT, config.SCENARIOS or "all")
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
