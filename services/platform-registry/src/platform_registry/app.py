"""
app.py
------

Entry point for platform-registry: owns the `platform` Postgres schema
(scenarios/entitlements) and seeds it from ../../scenarios/*.yaml on startup.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from pydantic import ValidationError

from platform_registry import get_env_config
from platform_registry.api import router
from platform_registry.core.db import init_engine
from platform_registry.core.logger import configure_logger, get_logger
from platform_registry.core.models import Base
from platform_registry.core.seed import seed_scenarios

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

    logger.success("platform-registry starting on port {}", config.HTTP_PORT)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
