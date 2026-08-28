"""
app.py
------

Entry point for rag-agent: a long-running FastAPI service serving
POST /chat/{scenario_slug} for every conversational_rag scenario in SCENARIOS
(empty/unset = all), shared across every tenant (retrieval is always scoped to the
caller's own Qdrant collection; completions go through llm-gateway, via a LangChain
tool-calling agent — see core/agent.py — never a raw provider SDK).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from ai_circus_shared.conversations import Base as ConversationsBase
from ai_circus_shared.conversations import init_engine
from ai_circus_shared.deployment_guard import enforce_safe_for_public_deployment
from ai_circus_shared.embeddings import build_embedding_provider
from ai_circus_shared.observability import configure_metrics
from ai_circus_shared.scenario_schema import resolve_scenarios
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from qdrant_client import QdrantClient

from rag_agent import get_env_config
from rag_agent.api import router
from rag_agent.core.logger import configure_logger, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Resolve SCENARIOS to their definitions, build the shared embedder, connect Qdrant/llm-gateway."""
    config = get_env_config()
    definitions = resolve_scenarios(Path(config.SCENARIOS_DIR), config.SCENARIOS, kind="conversational_rag")
    if not definitions:
        raise RuntimeError(
            f"No conversational_rag scenario matched SCENARIOS={config.SCENARIOS!r} under {config.SCENARIOS_DIR!r}."
        )

    # This instance's own conversations/messages tables (persisted chat history for
    # the UI's conversation sidebar) — a dedicated database, not platform-registry's.
    conversations_engine = init_engine(config)
    ConversationsBase.metadata.create_all(conversations_engine)

    # One provider shared by every scenario this instance serves — it must be the
    # same provider/model etl-vectorize embedded documents with, or retrieval
    # silently breaks (see ai_circus_shared.embeddings' module docstring).
    embedder = build_embedding_provider(
        provider=config.EMBEDDING_PROVIDER or "local",
        model_name=config.EMBEDDING_MODEL,
        google_api_key=config.GOOGLE_API_KEY.get_secret_value() if config.GOOGLE_API_KEY else None,
        voyage_api_key=config.VOYAGE_API_KEY.get_secret_value() if config.VOYAGE_API_KEY else None,
        llm_gateway_url=config.LLM_GATEWAY_URL,
        llm_gateway_api_key=config.LLM_GATEWAY_API_KEY.get_secret_value(),
    )

    app.state.definitions = definitions
    app.state.embedder = embedder
    app.state.qdrant = QdrantClient(url=config.QDRANT_URL)
    # One ChatOpenAI client per model_name, built lazily by api._llm() as requests pick
    # different models from platform-registry's live Settings picker — not a single
    # client built here, since which model to use can now change without a restart.
    app.state.llm_clients = {}

    yield


# FastAPI/Swagger UI docs are only useful for local iteration — staging/production
# traffic goes through Traefik and shouldn't expose an interactive API explorer.
_DOCS_ENABLED = os.getenv("APP_ENVIRONMENT", "local") in {"local", "docker"}

app = FastAPI(
    title="rag-agent",
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

    logger.success("rag-agent starting on port {} for SCENARIOS={!r}", config.HTTP_PORT, config.SCENARIOS or "all")
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
