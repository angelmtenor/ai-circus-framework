"""
app.py
------

Entry point for training: a one-shot job that loads a tabular_ml scenario's
normalized parquet from MinIO (written by etl-tabular), trains/selects a model per
the scenario's Green Code candidate policy, builds a SHAP explainer, and writes both
back to MinIO for the `prediction` service to load. Runs once and exits — not a
long-running server (see docker-compose.yml's `profiles: ["pipeline"]`).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import joblib
import pandas as pd
from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import (
    MODEL_EXPLAINER_KEY,
    MODEL_METADATA_KEY,
    MODEL_PIPELINE_KEY,
    NORMALIZED_DATASET_KEY,
)
from pydantic import ValidationError
from sklearn.model_selection import train_test_split

from training import get_env_config
from training.core.logger import configure_logger, get_logger
from training.core.training import (
    build_explainer,
    select_best_candidate,
    split_features,
    train_candidate,
    transformed_feature_names,
)

logger = get_logger(__name__)


def main() -> None:
    """Validate configuration, then train/select a model and save it with its explainer."""
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
    if definition.dataset is None or definition.model is None:
        logger.error("Scenario {!r} has no dataset/model config — is it a tabular_ml scenario?", config.SCENARIO_SLUG)
        sys.exit(1)

    store = ObjectStore.connect(
        bucket=definition.dataset.bucket,
        endpoint_url=config.MINIO_ENDPOINT,
        access_key=config.MINIO_ACCESS_KEY,
        secret_key=config.MINIO_SECRET_KEY.get_secret_value(),
    )

    df = pd.read_parquet(io.BytesIO(store.get(config.ORG_ID, NORMALIZED_DATASET_KEY)))
    x = df.loc[:, definition.dataset.feature_columns]
    y = df[definition.dataset.target]
    numeric_features, categorical_features = split_features(x, definition.dataset.feature_columns)

    x_train, x_test, y_train, y_test = train_test_split(x, y, test_size=0.2, random_state=0, stratify=y)

    candidates = [
        train_candidate(name, x_train, y_train, x_test, y_test, numeric_features, categorical_features)
        for name in definition.model.candidates
    ]
    best = select_best_candidate(candidates, definition.model.accuracy_gain_threshold_for_complexity)
    logger.success("Selected model: {!r} (test accuracy={:.4f})", best.name, best.test_accuracy)

    # Refit the selected model on the full dataset for the final artifact.
    best.pipeline.fit(x, y)
    explainer = build_explainer(best.pipeline, x)

    pipeline_buffer = io.BytesIO()
    joblib.dump(best.pipeline, pipeline_buffer, compress=True)
    store.put(config.ORG_ID, MODEL_PIPELINE_KEY, pipeline_buffer.getvalue())

    explainer_buffer = io.BytesIO()
    joblib.dump(explainer, explainer_buffer, compress=True)
    store.put(config.ORG_ID, MODEL_EXPLAINER_KEY, explainer_buffer.getvalue())

    metadata = {
        "scenario_slug": config.SCENARIO_SLUG,
        "model_name": best.name,
        "test_accuracy": best.test_accuracy,
        "candidates_evaluated": [c.name for c in candidates],
        "feature_columns": definition.dataset.feature_columns,
        "transformed_feature_names": transformed_feature_names(best.pipeline),
        "target": definition.dataset.target,
    }
    store.put(config.ORG_ID, MODEL_METADATA_KEY, json.dumps(metadata, indent=2).encode())

    logger.success("training finished for scenario={} org={}", config.SCENARIO_SLUG, config.ORG_ID)


if __name__ == "__main__":
    main()
