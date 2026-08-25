"""
app.py
------

Entry point for training: a one-shot job that, for every tabular_ml scenario in
SCENARIOS (empty/unset = all), loads the tenant's normalized parquet from SeaweedFS
(written by etl-tabular), trains/selects a model per the scenario's Green Code
candidate policy, builds a SHAP explainer, and writes both back to SeaweedFS for the
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
    MODEL_CHECKSUMS_METADATA_FIELD,
    MODEL_EXPLAINER_KEY,
    MODEL_METADATA_KEY,
    MODEL_PIPELINE_KEY,
    MODEL_PIPELINE_LOWER_KEY,
    MODEL_PIPELINE_UPPER_KEY,
    NORMALIZED_DATASET_KEY,
    artifact_checksum,
)
from pydantic import ValidationError
from sklearn.model_selection import train_test_split

from training import get_env_config
from training.core.logger import configure_logger, get_logger
from training.core.training import (
    build_explainer,
    fit_quantile_pipelines,
    global_shap_importance,
    select_best_candidate,
    split_features,
    train_candidate,
    transformed_feature_names,
)
from training.data_model import EnvConfig

logger = get_logger(__name__)


def _dump(obj: object) -> bytes:
    """Serialize a fitted pipeline/explainer to compressed joblib bytes."""
    buffer = io.BytesIO()
    joblib.dump(obj, buffer, compress=True)
    return buffer.getvalue()


def _train_one(config: EnvConfig, slug: str, definition: ScenarioDefinition) -> None:
    """Train/select a model for one scenario and save it + its explainer to SeaweedFS."""
    assert definition.dataset is not None and definition.model is not None  # guaranteed by kind filter

    store = ObjectStore.connect(
        bucket=definition.dataset.bucket,
        endpoint_url=config.OBJECT_STORE_ENDPOINT,
        access_key=config.OBJECT_STORE_ACCESS_KEY,
        secret_key=config.OBJECT_STORE_SECRET_KEY.get_secret_value(),
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
    feature_importance = global_shap_importance(best.pipeline, explainer, x, definition.dataset.feature_columns)

    checksums: dict[str, str] = {}

    pipeline_bytes = _dump(best.pipeline)
    store.put(config.ORG_ID, MODEL_PIPELINE_KEY, pipeline_bytes)
    checksums["pipeline"] = artifact_checksum(pipeline_bytes)

    explainer_bytes = _dump(explainer)
    store.put(config.ORG_ID, MODEL_EXPLAINER_KEY, explainer_bytes)
    checksums["explainer"] = artifact_checksum(explainer_bytes)

    has_intervals = definition.model.task_type == "regression"
    if has_intervals:
        # pyrefly: ignore [bad-argument-type]
        pipeline_lower, pipeline_upper = fit_quantile_pipelines(numeric_features, categorical_features, x, y)
        lower_bytes = _dump(pipeline_lower)
        store.put(config.ORG_ID, MODEL_PIPELINE_LOWER_KEY, lower_bytes)
        checksums["pipeline_lower"] = artifact_checksum(lower_bytes)

        upper_bytes = _dump(pipeline_upper)
        store.put(config.ORG_ID, MODEL_PIPELINE_UPPER_KEY, upper_bytes)
        checksums["pipeline_upper"] = artifact_checksum(upper_bytes)
        logger.success("90% prediction interval models trained for scenario={} org={}", slug, config.ORG_ID)

    # Written last, once every artifact above is confirmed uploaded — prediction's
    # model_cache treats this as the manifest: it won't serve an artifact whose bytes
    # don't match the checksum recorded here (see MODEL_CHECKSUMS_METADATA_FIELD).
    metadata = {
        "scenario_slug": slug,
        "model_name": best.name,
        "test_score": best.test_score,
        "task_type": definition.model.task_type,
        "candidates_evaluated": [c.name for c in candidates],
        "feature_columns": definition.dataset.feature_columns,
        "transformed_feature_names": transformed_feature_names(best.pipeline),
        "global_feature_importance": feature_importance,
        "target": definition.dataset.target,
        "has_intervals": has_intervals,
        MODEL_CHECKSUMS_METADATA_FIELD: checksums,
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

    failed_slugs: list[str] = []
    for slug, definition in definitions.items():
        try:
            _train_one(config, slug, definition)
        except Exception:
            # One scenario's data/training bug (e.g. a dtype the pipeline can't
            # handle) must not cost every scenario after it its model artifacts —
            # log and keep going, then fail the run at the end so CI/operators
            # still notice.
            logger.exception("Training failed for scenario={} — continuing with remaining scenarios", slug)
            failed_slugs.append(slug)

    if failed_slugs:
        logger.error("Training failed for {} scenario(s): {}", len(failed_slugs), ", ".join(failed_slugs))
        sys.exit(1)


if __name__ == "__main__":
    main()
