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

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from ai_circus_shared.scenario_schema import resolve_scenarios
from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from pydantic import ValidationError
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from rag_agent import get_env_config
from rag_agent.api import router
from rag_agent.core.logger import configure_logger, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Resolve SCENARIOS to their definitions, load one embedder per scenario, connect Qdrant/llm-gateway."""
    config = get_env_config()
    definitions = resolve_scenarios(Path(config.SCENARIOS_DIR), config.SCENARIOS, kind="conversational_rag")
    if not definitions:
        raise RuntimeError(
            f"No conversational_rag scenario matched SCENARIOS={config.SCENARIOS!r} under {config.SCENARIOS_DIR!r}."
        )

    embedders = {}
    for slug, definition in definitions.items():
        assert definition.documents is not None  # guaranteed by kind="conversational_rag" filter
        embedders[slug] = SentenceTransformer(definition.documents.embedding.model)

    app.state.definitions = definitions
    app.state.embedders = embedders
    app.state.qdrant = QdrantClient(url=config.QDRANT_URL)
    llm_api_key = config.LLM_GATEWAY_API_KEY.get_secret_value()
    app.state.llm = ChatOpenAI(base_url=config.LLM_GATEWAY_URL, api_key=llm_api_key, model=config.LLM_MODEL)

    yield


app = FastAPI(title="rag-agent", lifespan=lifespan)
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

    logger.success("rag-agent starting on port {} for SCENARIOS={!r}", config.HTTP_PORT, config.SCENARIOS or "all")
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
