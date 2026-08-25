"""
app.py
------

Entry point for etl-vectorize: a one-shot job that, for every conversational_rag
scenario in SCENARIOS (empty/unset = all) plus every assisted_form scenario that
configures a document catalog (`form.classification_field` set), extracts the
tenant's documents from SeaweedFS
(bootstrapping them on first run from either a tracked sample_docs/ folder or a public
GitHub repo folder — see `documents.seed_prefix`/`documents.github_source`), chunks and
embeds them, and upserts the result into the tenant's Qdrant collection. Runs once
and exits — not a long-running server (see docker-compose.yml's `profiles: ["pipeline"]`).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from pathlib import Path

from ai_circus_shared.embeddings import build_embedding_provider
from ai_circus_shared.scenario_schema import resolve_scenarios
from ai_circus_shared.storage import ObjectStore
from pydantic import ValidationError
from qdrant_client import QdrantClient

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
    # assisted_form scenarios are optional here: only those that configure RAG
    # classification (`form.classification_field`) set `documents` at all — a plain
    # slot-filling assisted_form scenario has nothing for this job to vectorize.
    form_definitions = {
        slug: d
        for slug, d in resolve_scenarios(scenarios_dir, config.SCENARIOS, kind="assisted_form").items()
        if d.documents is not None
    }
    definitions = {**resolve_scenarios(scenarios_dir, config.SCENARIOS, kind="conversational_rag"), **form_definitions}
    if not definitions:
        logger.error(
            "No conversational_rag or documents-configured assisted_form scenario matched SCENARIOS={!r} under {!r}.",
            config.SCENARIOS,
            config.SCENARIOS_DIR,
        )
        sys.exit(1)

    # One provider for every scenario this run processes — it must be the same
    # provider/model rag-agent embeds queries with, or retrieval silently breaks
    # (see ai_circus_shared.embeddings' module docstring).
    provider = build_embedding_provider(
        provider=config.EMBEDDING_PROVIDER or "local",
        model_name=config.EMBEDDING_MODEL,
        google_api_key=config.GOOGLE_API_KEY.get_secret_value() if config.GOOGLE_API_KEY else None,
        voyage_api_key=config.VOYAGE_API_KEY.get_secret_value() if config.VOYAGE_API_KEY else None,
        llm_gateway_url=config.LLM_GATEWAY_URL,
        llm_gateway_api_key=config.LLM_GATEWAY_API_KEY.get_secret_value(),
    )

    for slug, definition in definitions.items():
        assert definition.documents is not None and definition.vector_store is not None  # guaranteed by kind filter
        store = ObjectStore.connect(
            bucket=definition.documents.bucket,
            endpoint_url=config.OBJECT_STORE_ENDPOINT,
            access_key=config.OBJECT_STORE_ACCESS_KEY,
            secret_key=config.OBJECT_STORE_SECRET_KEY.get_secret_value(),
        )
        qdrant = QdrantClient(url=config.QDRANT_URL)

        run_vectorize(
            store, qdrant, provider, config.ORG_ID, definition.documents, definition.vector_store, scenarios_dir / slug
        )
        logger.success("etl-vectorize finished for scenario={} org={}", slug, config.ORG_ID)


if __name__ == "__main__":
    main()
