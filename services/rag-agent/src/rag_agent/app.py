"""
app.py
------

Entry point for rag-agent: a long-running FastAPI service serving POST /chat for one
conversational_rag scenario, shared across every tenant (retrieval is always scoped
to the caller's own Qdrant collection; completions go through llm-gateway, never a
raw provider SDK).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from ai_circus_shared.scenario_schema import ScenarioDefinition
from fastapi import FastAPI
from openai import OpenAI
from pydantic import ValidationError
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from rag_agent import get_env_config
from rag_agent.api import router
from rag_agent.core.logger import configure_logger, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect to Qdrant/llm-gateway and load the embedding model + vector-store config."""
    config = get_env_config()
    scenario_dir = Path(config.SCENARIOS_DIR) / config.SCENARIO_SLUG
    definition = ScenarioDefinition.load(scenario_dir / "scenario.yaml")
    if definition.documents is None or definition.vector_store is None:
        raise RuntimeError(
            f"Scenario {config.SCENARIO_SLUG!r} has no documents/vector_store config — "
            "is it a conversational_rag scenario?"
        )

    app.state.qdrant = QdrantClient(url=config.QDRANT_URL)
    app.state.embedding_model = SentenceTransformer(definition.documents.embedding.model)
    app.state.vector_store = definition.vector_store
    llm_api_key = config.LLM_GATEWAY_API_KEY.get_secret_value()
    app.state.llm_client = OpenAI(base_url=config.LLM_GATEWAY_URL, api_key=llm_api_key)

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

    logger.success("rag-agent starting on port {} for scenario={}", config.HTTP_PORT, config.SCENARIO_SLUG)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
