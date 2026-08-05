"""
app.py
------

Entry point for etl-tabular: a one-shot job that extracts a tabular_ml scenario's raw
dataset from MinIO (bootstrapping it from a tracked sample file on first run), cleans
it, and writes the normalized parquet back to MinIO. Runs once and exits — not a
long-running server (see docker-compose.yml's `profiles: ["pipeline"]`).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from pathlib import Path

from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore
from pydantic import ValidationError

from etl_tabular import get_env_config
from etl_tabular.core.etl import run_etl
from etl_tabular.core.logger import configure_logger, get_logger

logger = get_logger(__name__)


def main() -> None:
    """Validate configuration, then run the extract -> transform -> load pipeline."""
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
    if definition.dataset is None:
        logger.error("Scenario {!r} has no dataset config — is it a tabular_ml scenario?", config.SCENARIO_SLUG)
        sys.exit(1)

    store = ObjectStore.connect(
        bucket=definition.dataset.bucket,
        endpoint_url=config.MINIO_ENDPOINT,
        access_key=config.MINIO_ACCESS_KEY,
        secret_key=config.MINIO_SECRET_KEY.get_secret_value(),
    )

    run_etl(store, config.ORG_ID, definition.dataset, scenario_dir)
    logger.success("etl-tabular finished for scenario={} org={}", config.SCENARIO_SLUG, config.ORG_ID)


if __name__ == "__main__":
    main()
