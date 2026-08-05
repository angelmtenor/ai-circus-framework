"""
app.py
------

Entry point for prediction: a long-running FastAPI service serving POST /predict for
one tabular_ml scenario, shared across every tenant (model/explainer are loaded and
cached per-org from MinIO on first request).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore
from fastapi import FastAPI
from pydantic import ValidationError

from prediction import get_env_config
from prediction.api import router
from prediction.core.logger import configure_logger, get_logger
from prediction.core.model_cache import ModelCache

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect to the scenario's MinIO bucket and set up the per-tenant model cache."""
    config = get_env_config()
    scenario_dir = Path(config.SCENARIOS_DIR) / config.SCENARIO_SLUG
    definition = ScenarioDefinition.load(scenario_dir / "scenario.yaml")
    if definition.dataset is None:
        raise RuntimeError(f"Scenario {config.SCENARIO_SLUG!r} has no dataset config — is it a tabular_ml scenario?")

    store = ObjectStore.connect(
        bucket=definition.dataset.bucket,
        endpoint_url=config.MINIO_ENDPOINT,
        access_key=config.MINIO_ACCESS_KEY,
        secret_key=config.MINIO_SECRET_KEY.get_secret_value(),
    )
    app.state.model_cache = ModelCache(store)

    yield


app = FastAPI(title="prediction", lifespan=lifespan)
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

    logger.success("prediction starting on port {} for scenario={}", config.HTTP_PORT, config.SCENARIO_SLUG)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
