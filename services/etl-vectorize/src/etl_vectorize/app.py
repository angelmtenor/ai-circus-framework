"""
app.py
------

Entry point for etl-vectorize: a one-shot job that, for every conversational_rag
scenario in SCENARIOS (empty/unset = all), extracts the tenant's documents from MinIO
(bootstrapping them from a tracked sample_docs/ folder on first run), chunks and
embeds them, and upserts the result into the tenant's Qdrant collection. Runs once
and exits — not a long-running server (see docker-compose.yml's `profiles: ["pipeline"]`).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from pathlib import Path

from ai_circus_shared.scenario_schema import resolve_scenarios
from ai_circus_shared.storage import ObjectStore
from pydantic import ValidationError
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from etl_vectorize import get_env_config
from etl_vectorize.core.logger import configure_logger, get_logger
from etl_vectorize.core.vectorize import run_vectorize

logger = get_logger(__name__)


def main() -> None:
    """Validate configuration, then run the extract -> chunk -> embed -> load pipeline per scenario."""
    configure_logger()

    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)

    scenarios_dir = Path(config.SCENARIOS_DIR)
    definitions = resolve_scenarios(scenarios_dir, config.SCENARIOS, kind="conversational_rag")
    if not definitions:
        logger.error(
            "No conversational_rag scenario matched SCENARIOS={!r} under {!r}.", config.SCENARIOS, config.SCENARIOS_DIR
        )
        sys.exit(1)

    for slug, definition in definitions.items():
        assert definition.documents is not None and definition.vector_store is not None  # guaranteed by kind filter
        store = ObjectStore.connect(
            bucket=definition.documents.bucket,
            endpoint_url=config.MINIO_ENDPOINT,
            access_key=config.MINIO_ACCESS_KEY,
            secret_key=config.MINIO_SECRET_KEY.get_secret_value(),
        )
        qdrant = QdrantClient(url=config.QDRANT_URL)
        model = SentenceTransformer(definition.documents.embedding.model)

        run_vectorize(
            store, qdrant, model, config.ORG_ID, definition.documents, definition.vector_store, scenarios_dir / slug
        )
        logger.success("etl-vectorize finished for scenario={} org={}", slug, config.ORG_ID)


if __name__ == "__main__":
    main()
