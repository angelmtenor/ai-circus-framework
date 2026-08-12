"""
app.py
------

Entry point for assistant: a long-running FastAPI service serving
POST /agui/{scenario_slug} for every tabular_ml scenario in SCENARIOS (empty/unset =
all), shared across every tenant (grounding system prompts are loaded and cached per
(org, scenario) from MinIO on first request — see core/prompt_cache.py; all
completions go through llm-gateway, never a raw provider SDK).

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

from assistant import get_env_config
from assistant.api import router
from assistant.core.logger import configure_logger, get_logger
from assistant.core.prompt_cache import SystemPromptCache

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Resolve SCENARIOS to their definitions, connect each one's MinIO bucket, and llm-gateway."""
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
    app.state.prompt_cache = SystemPromptCache(stores, definitions, fallback_org_id=config.SHARED_MODEL_ORG_ID)
    # LangChain ChatOpenAI client backing the AG-UI route's create_agent (see
    # core/agent.py) — only a LangChain/LangGraph agent can participate in generative
    # UI. Cached per model_name, same reasoning as rag-agent's llm_clients — which
    # model to use can change without a restart.
    app.state.chat_llm_clients = {}

    yield


app = FastAPI(title="assistant", lifespan=lifespan)
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

    # ui-react calls this API directly from the browser (never via cookies, always a
    # Bearer token, so no CSRF risk) — still scoped to CORS_ALLOWED_ORIGINS rather than
    # "*" so a token that leaked to some other origin can't be replayed cross-origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in config.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logger.success("assistant starting on port {} for SCENARIOS={!r}", config.HTTP_PORT, config.SCENARIOS or "all")
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
