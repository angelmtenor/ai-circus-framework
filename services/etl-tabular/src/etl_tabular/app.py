"""
app.py
------

Entry point for etl-tabular: a one-shot job that, for every tabular_ml scenario in
SCENARIOS (empty/unset = all), extracts the tenant's raw dataset from SeaweedFS
(bootstrapping it from a tracked sample file on first run), cleans it, and writes the
normalized parquet back to SeaweedFS. Runs once and exits — not a long-running server
(see docker-compose.yml's `profiles: ["pipeline"]`).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from pathlib import Path

from ai_circus_shared.scenario_schema import resolve_scenarios
from ai_circus_shared.storage import ObjectStore
from pydantic import ValidationError

from etl_tabular import get_env_config
from etl_tabular.core.etl import run_etl
from etl_tabular.core.logger import configure_logger, get_logger

logger = get_logger(__name__)


def main() -> None:
    """Validate configuration, then run the extract -> transform -> load pipeline per scenario."""
    configure_logger()

    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)

    scenarios_dir = Path(config.SCENARIOS_DIR)
    definitions = resolve_scenarios(scenarios_dir, config.SCENARIOS, kind="tabular_ml")
    if not definitions:
        logger.error(
            "No tabular_ml scenario matched SCENARIOS={!r} under {!r}.", config.SCENARIOS, config.SCENARIOS_DIR
        )
        sys.exit(1)

    for slug, definition in definitions.items():
        assert definition.dataset is not None  # guaranteed by kind="tabular_ml" filter
        store = ObjectStore.connect(
            bucket=definition.dataset.bucket,
            endpoint_url=config.OBJECT_STORE_ENDPOINT,
            access_key=config.OBJECT_STORE_ACCESS_KEY,
            secret_key=config.OBJECT_STORE_SECRET_KEY.get_secret_value(),
        )
        run_etl(store, config.ORG_ID, definition.dataset, scenarios_dir / slug)
        logger.success("etl-tabular finished for scenario={} org={}", slug, config.ORG_ID)


if __name__ == "__main__":
    main()
