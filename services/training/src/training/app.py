"""
app.py
------

Entry point for training: a one-shot job that, for every tabular_ml scenario in
SCENARIOS (empty/unset = all), loads the tenant's normalized parquet from MinIO
(written by etl-tabular), trains/selects a model per the scenario's Green Code
candidate policy, builds a SHAP explainer, and writes both back to MinIO for the
`prediction` service to load. Runs once and exits — not a long-running server (see
docker-compose.yml's `profiles: ["pipeline"]`).

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import joblib
import pandas as pd
from ai_circus_shared.scenario_schema import ScenarioDefinition, resolve_scenarios
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import (
    MODEL_EXPLAINER_KEY,
    MODEL_METADATA_KEY,
    MODEL_PIPELINE_KEY,
    MODEL_PIPELINE_LOWER_KEY,
    MODEL_PIPELINE_UPPER_KEY,
    NORMALIZED_DATASET_KEY,
)
from pydantic import ValidationError
from sklearn.model_selection import train_test_split

from training import get_env_config
from training.core.logger import configure_logger, get_logger
from training.core.training import (
    build_explainer,
    fit_quantile_pipelines,
    select_best_candidate,
    split_features,
    train_candidate,
    transformed_feature_names,
)
from training.data_model import EnvConfig

logger = get_logger(__name__)


def _train_one(config: EnvConfig, slug: str, definition: ScenarioDefinition) -> None:
    """Train/select a model for one scenario and save it + its explainer to MinIO."""
    assert definition.dataset is not None and definition.model is not None  # guaranteed by kind filter

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

    # stratify=y requires discrete classes — not meaningful (and not possible) for a
    # continuous regression target.
    stratify = y if definition.model.task_type == "classification" else None
    x_train, x_test, y_train, y_test = train_test_split(x, y, test_size=0.2, random_state=0, stratify=stratify)

    candidates = [
        train_candidate(
            name, x_train, y_train, x_test, y_test, numeric_features, categorical_features, definition.model.task_type
        )
        for name in definition.model.candidates
    ]
    best = select_best_candidate(candidates, definition.model.accuracy_gain_threshold_for_complexity)
    logger.success("Selected model: {!r} (test score={:.4f})", best.name, best.test_score)

    # Refit the selected model on the full dataset for the final artifact.
    best.pipeline.fit(x, y)
    explainer = build_explainer(best.pipeline, x)

    pipeline_buffer = io.BytesIO()
    joblib.dump(best.pipeline, pipeline_buffer, compress=True)
    store.put(config.ORG_ID, MODEL_PIPELINE_KEY, pipeline_buffer.getvalue())

    explainer_buffer = io.BytesIO()
    joblib.dump(explainer, explainer_buffer, compress=True)
    store.put(config.ORG_ID, MODEL_EXPLAINER_KEY, explainer_buffer.getvalue())

    has_intervals = definition.model.task_type == "regression"
    if has_intervals:
        pipeline_lower, pipeline_upper = fit_quantile_pipelines(numeric_features, categorical_features, x, y)
        lower_buffer = io.BytesIO()
        joblib.dump(pipeline_lower, lower_buffer, compress=True)
        store.put(config.ORG_ID, MODEL_PIPELINE_LOWER_KEY, lower_buffer.getvalue())
        upper_buffer = io.BytesIO()
        joblib.dump(pipeline_upper, upper_buffer, compress=True)
        store.put(config.ORG_ID, MODEL_PIPELINE_UPPER_KEY, upper_buffer.getvalue())
        logger.success("90% prediction interval models trained for scenario={} org={}", slug, config.ORG_ID)

    metadata = {
        "scenario_slug": slug,
        "model_name": best.name,
        "test_score": best.test_score,
        "task_type": definition.model.task_type,
        "candidates_evaluated": [c.name for c in candidates],
        "feature_columns": definition.dataset.feature_columns,
        "transformed_feature_names": transformed_feature_names(best.pipeline),
        "target": definition.dataset.target,
        "has_intervals": has_intervals,
    }
    store.put(config.ORG_ID, MODEL_METADATA_KEY, json.dumps(metadata, indent=2).encode())

    logger.success("training finished for scenario={} org={}", slug, config.ORG_ID)


def main() -> None:
    """Validate configuration, then train/select a model per scenario."""
    configure_logger()

    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)

    definitions = resolve_scenarios(Path(config.SCENARIOS_DIR), config.SCENARIOS, kind="tabular_ml")
    if not definitions:
        logger.error(
            "No tabular_ml scenario matched SCENARIOS={!r} under {!r}.", config.SCENARIOS, config.SCENARIOS_DIR
        )
        sys.exit(1)

    for slug, definition in definitions.items():
        _train_one(config, slug, definition)


if __name__ == "__main__":
    main()
