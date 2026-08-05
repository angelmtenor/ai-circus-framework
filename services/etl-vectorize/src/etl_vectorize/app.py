"""
app.py
------

Entry point for etl-vectorize: a one-shot job that extracts a conversational_rag
scenario's documents from MinIO (bootstrapping them from a tracked sample_docs/
folder on first run), chunks and embeds them, and upserts the result into the
tenant's Qdrant collection. Runs once and exits — not a long-running server (see
docker-compose.yml's `profiles: ["pipeline"]`).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from pathlib import Path

from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore
from pydantic import ValidationError
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from etl_vectorize import get_env_config
from etl_vectorize.core.logger import configure_logger, get_logger
from etl_vectorize.core.vectorize import run_vectorize

logger = get_logger(__name__)


def main() -> None:
    """Validate configuration, then run the extract -> chunk -> embed -> load pipeline."""
    configure_logger()

    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)

    scenario_dir = Path(config.SCENARIOS_DIR) / config.SCENARIO_SLUG
    definition = ScenarioDefinition.load(scenario_dir / "scenario.yaml")
    if definition.documents is None or definition.vector_store is None:
        logger.error(
            "Scenario {!r} has no documents/vector_store config — is it a conversational_rag scenario?",
            config.SCENARIO_SLUG,
        )
        sys.exit(1)

    store = ObjectStore.connect(
        bucket=definition.documents.bucket,
        endpoint_url=config.MINIO_ENDPOINT,
        access_key=config.MINIO_ACCESS_KEY,
        secret_key=config.MINIO_SECRET_KEY.get_secret_value(),
    )
    qdrant = QdrantClient(url=config.QDRANT_URL)
    model = SentenceTransformer(definition.documents.embedding.model)

    run_vectorize(store, qdrant, model, config.ORG_ID, definition.documents, definition.vector_store, scenario_dir)
    logger.success("etl-vectorize finished for scenario={} org={}", config.SCENARIO_SLUG, config.ORG_ID)


if __name__ == "__main__":
    main()
