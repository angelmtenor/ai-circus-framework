"""
app.py
------

Entry point for form-agent: a long-running FastAPI service serving
POST /agui/{scenario_slug} and POST /submissions/{scenario_slug} for every
assisted_form scenario in SCENARIOS (empty/unset = all). A LangChain tool-calling
agent (see core/agent.py) fills the form via a frontend-declared tool and, only for
scenarios that configure `form.classification_field`, classifies the request via
retrieval over the tenant's vectorized document catalog (same embedder/Qdrant pattern
as rag-agent). Completions always go through llm-gateway, never a raw provider SDK.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from ai_circus_shared.embeddings import build_embedding_provider
from ai_circus_shared.scenario_schema import resolve_scenarios
from ai_circus_shared.storage import ObjectStore
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from qdrant_client import QdrantClient

from form_agent import get_env_config
from form_agent.api import router
from form_agent.core.logger import configure_logger, get_logger
from form_agent.core.submissions import SUBMISSIONS_BUCKET

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Resolve SCENARIOS to their definitions, build the shared embedder, connect Qdrant/MinIO/llm-gateway."""
    config = get_env_config()
    definitions = resolve_scenarios(Path(config.SCENARIOS_DIR), config.SCENARIOS, kind="assisted_form")
    if not definitions:
        raise RuntimeError(
            f"No assisted_form scenario matched SCENARIOS={config.SCENARIOS!r} under {config.SCENARIOS_DIR!r}."
        )

    # Built unconditionally, same as rag-agent — cheap relative to the model
    # download/load it triggers once, and this instance may serve a mix of
    # classification-driven and plain slot-filling scenarios. Must be the same
    # provider/model etl-vectorize embedded documents with, or retrieval silently
    # breaks (see ai_circus_shared.embeddings' module docstring).
    embedder = build_embedding_provider(
        provider=config.EMBEDDING_PROVIDER or "local",
        model_name=config.EMBEDDING_MODEL,
        google_api_key=config.GOOGLE_API_KEY.get_secret_value() if config.GOOGLE_API_KEY else None,
        voyage_api_key=config.VOYAGE_API_KEY.get_secret_value() if config.VOYAGE_API_KEY else None,
    )

    app.state.definitions = definitions
    app.state.embedder = embedder
    app.state.qdrant = QdrantClient(url=config.QDRANT_URL)
    app.state.store = ObjectStore.connect(
        bucket=SUBMISSIONS_BUCKET,
        endpoint_url=config.MINIO_ENDPOINT,
        access_key=config.MINIO_ACCESS_KEY,
        secret_key=config.MINIO_SECRET_KEY.get_secret_value(),
    )
    # One ChatOpenAI client per model_name, built lazily by api._llm() as requests pick
    # different models from platform-registry's live Settings picker — not a single
    # client built here, since which model to use can now change without a restart.
    app.state.llm_clients = {}

    yield


app = FastAPI(title="form-agent", lifespan=lifespan)
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

    logger.success("form-agent starting on port {} for SCENARIOS={!r}", config.HTTP_PORT, config.SCENARIOS or "all")
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
