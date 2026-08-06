"""
app.py
------

Entry point for prediction: a long-running FastAPI service serving
POST /predict/{scenario_slug} for every tabular_ml scenario in SCENARIOS (empty/unset
= all), shared across every tenant (model/explainer are loaded and cached per
(org, scenario) from MinIO on first request — see core/model_cache.py).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from ai_circus_shared.scenario_schema import resolve_scenarios
from ai_circus_shared.storage import ObjectStore
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from prediction import get_env_config
from prediction.api import router
from prediction.core.logger import configure_logger, get_logger
from prediction.core.model_cache import ModelCache

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Resolve SCENARIOS to their definitions and connect to each one's MinIO bucket."""
    config = get_env_config()
    definitions = resolve_scenarios(Path(config.SCENARIOS_DIR), config.SCENARIOS, kind="tabular_ml")
    if not definitions:
        raise RuntimeError(
            f"No tabular_ml scenario matched SCENARIOS={config.SCENARIOS!r} under {config.SCENARIOS_DIR!r}."
        )

    stores = {}
    for slug, definition in definitions.items():
        assert definition.dataset is not None  # guaranteed by kind="tabular_ml" filter
        stores[slug] = ObjectStore.connect(
            bucket=definition.dataset.bucket,
            endpoint_url=config.MINIO_ENDPOINT,
            access_key=config.MINIO_ACCESS_KEY,
            secret_key=config.MINIO_SECRET_KEY.get_secret_value(),
        )

    app.state.definitions = definitions
    app.state.model_cache = ModelCache(stores)

    yield


app = FastAPI(title="prediction", lifespan=lifespan)
# ui-react calls this API directly from the browser (never via cookies, always a
# Bearer token), so a wildcard origin carries no CSRF/credential risk here.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
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

    logger.success("prediction starting on port {} for SCENARIOS={!r}", config.HTTP_PORT, config.SCENARIOS or "all")
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
