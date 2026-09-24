"""
app.py
------

Entry point for dl-training: a one-shot job that fine-tunes, exports and publishes the
model of every `deep_learning` scenario in SCENARIOS (empty/unset = all), for ORG_ID.

- `dl-training` (this module's main) — the full pipeline, per scenario.
- `dl-training-download` — only fetch + verify + store each scenario's public raw data
  (`make dl-data`), e.g. to pre-seed SeaweedFS before an offline demo.

Deliberately NOT part of `make all`/`make k3s-all`: it runs from `make dl-train-*`
(host, GPU if present), `make k3s-dl-train-*` or the admin console's "Train" button.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from pathlib import Path

from ai_circus_shared.scenario_schema import ScenarioDefinition, resolve_scenarios
from ai_circus_shared.storage import ObjectStore
from pydantic import ValidationError

from dl_training import get_env_config
from dl_training.core import data, pipeline
from dl_training.core.device import resolve_device
from dl_training.core.logger import configure_logger, get_logger
from dl_training.data_model import EnvConfig

logger = get_logger(__name__)


def _load() -> tuple[EnvConfig, dict[str, ScenarioDefinition]]:
    """Validated config + the deep_learning scenarios this run covers (exits on error)."""
    configure_logger()
    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)
    definitions = resolve_scenarios(Path(config.SCENARIOS_DIR), config.SCENARIOS or "", kind="deep_learning")
    if not definitions:
        logger.error(
            "No deep_learning scenario matched SCENARIOS={!r} under {!r}", config.SCENARIOS, config.SCENARIOS_DIR
        )
        sys.exit(1)
    return config, definitions


def _store(config: EnvConfig, definition: ScenarioDefinition) -> ObjectStore:
    assert definition.deep_learning is not None
    return ObjectStore.connect(
        bucket=definition.deep_learning.bucket,
        endpoint_url=config.OBJECT_STORE_ENDPOINT,
        access_key=config.OBJECT_STORE_ACCESS_KEY,
        secret_key=config.OBJECT_STORE_SECRET_KEY.get_secret_value(),
    )


def main() -> None:
    """Train every selected scenario; exit 1 if any failed (the others still run)."""
    config, definitions = _load()
    cache_dir = Path(config.DL_CACHE_DIR).expanduser()
    try:
        device = resolve_device(config.DL_DEVICE or "auto")
    except (ValueError, RuntimeError) as e:
        logger.error(str(e))
        sys.exit(1)
    logger.info("dl-training on {} ({}) for {}", device.kind, device.name, ", ".join(definitions))

    failed = []
    for slug, definition in definitions.items():
        try:
            manifest = pipeline.train_scenario(
                definition,
                _store(config, definition),
                org_id=config.ORG_ID,
                device=device,
                cache_dir=cache_dir,
                tracking_uri=config.MLFLOW_TRACKING_URI,
            )
            logger.success(
                "{}: trained in {}s on {} — test {}",
                slug,
                manifest["training_seconds"],
                device.kind,
                manifest["evaluation"]["metrics"],
            )
        except Exception:
            logger.exception("{}: training failed", slug)
            failed.append(slug)
    if failed:
        logger.error("dl-training finished with failures: {}", ", ".join(failed))
        sys.exit(1)
    logger.success("dl-training finished: {}", ", ".join(definitions))


def download_main() -> None:
    """Fetch, verify and store every selected scenario's raw public data (no training)."""
    config, definitions = _load()
    cache_dir = Path(config.DL_CACHE_DIR).expanduser()
    for slug, definition in definitions.items():
        assert definition.deep_learning is not None
        paths = data.ensure_raw(_store(config, definition), config.ORG_ID, definition.deep_learning, cache_dir)
        logger.success("{}: raw data ready ({})", slug, ", ".join(str(p) for p in paths.values()))


if __name__ == "__main__":
    main()
