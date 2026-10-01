"""
app.py
------

Entry point for training: a one-shot job that, for every tabular_ml scenario in
SCENARIOS (empty/unset = all), loads the tenant's normalized parquet from SeaweedFS
(written by etl-tabular), trains/selects a model per the scenario's Green Code
candidate policy, builds a SHAP explainer, and writes both back to SeaweedFS for the
`prediction` service to load. Runs once and exits — not a long-running server (see
docker-compose.yml's `profiles: ["pipeline"]`).

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from ai_circus_shared.business_rules import passes
from ai_circus_shared.embeddings import GatewayEmbeddingProvider
from ai_circus_shared.scenario_schema import ScenarioDefinition, resolve_scenarios
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import (
    MAX_OUT_OF_FOLD_EXPLAINED_ROWS,
    MAX_OUT_OF_FOLD_ROWS,
    MODEL_CHALLENGER_EXPLAINER_KEY,
    MODEL_CHALLENGER_PIPELINE_KEY,
    MODEL_CHECKSUMS_METADATA_FIELD,
    MODEL_EXPLAINER_KEY,
    MODEL_METADATA_KEY,
    MODEL_OUT_OF_FOLD_KEY,
    MODEL_PIPELINE_KEY,
    MODEL_PIPELINE_LOWER_KEY,
    MODEL_PIPELINE_UPPER_KEY,
    NORMALIZED_DATASET_KEY,
    artifact_checksum,
)
from pydantic import ValidationError
from sklearn.model_selection import train_test_split

from training import get_env_config
from training.core.challenger import ChallengerResult, ChallengerUnavailableError, train_text_challenger
from training.core.logger import configure_logger, get_logger
from training.core.mlflow_tracking import log_training_run
from training.core.training import (
    DEFAULT_SELECTION_METRIC,
    build_explainer,
    fit_quantile_pipelines,
    global_shap_importance,
    holdout_evaluation,
    out_of_fold_scores,
    select_best_candidate,
    split_features,
    text_term_importance,
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
    df, rules_excluded_rows = _drop_rule_stopped_rows(df, definition)
    x = df.loc[:, definition.dataset.feature_columns]
    y = df[definition.dataset.target]
    text_features = definition.dataset.text_columns()
    numeric_features, categorical_features = split_features(x, definition.dataset.feature_columns, text_features)
    text_language = definition.dataset.text_language

    # stratify=y requires discrete classes — not meaningful (and not possible) for a
    # continuous regression target.
    stratify = y if definition.model.task_type == "classification" else None
    x_train, x_test, y_train, y_test = train_test_split(x, y, test_size=0.2, random_state=0, stratify=stratify)

    model = definition.model
    selection_metric = model.selection_metric or DEFAULT_SELECTION_METRIC[model.task_type]
    candidates = [
        train_candidate(
            name,
            x_train,
            y_train,
            x_test,
            y_test,
            numeric_features,
            categorical_features,
            model.task_type,
            selection_metric=selection_metric,
            cv_folds=model.cv_folds,
            text_features=text_features,
            text_language=text_language,
        )
        for name in model.candidates
    ]
    best = select_best_candidate(candidates, model.accuracy_gain_threshold_for_complexity)
    logger.success(
        "Selected model: {!r} ({}={:.4f}, test score={:.4f})",
        best.name,
        selection_metric,
        best.selection_score,
        best.test_score,
    )
    # Scored before the refit below — the only leakage-free ROC curve/confusion matrix
    # of the selected model (see holdout_evaluation's docstring).
    # pyrefly: ignore [bad-argument-type]
    evaluation = holdout_evaluation(best.pipeline, x_test, y_test) if model.task_type == "classification" else None

    # Before the refit: every row scored by a model that never saw it (opt-in).
    out_of_fold = _out_of_fold(
        slug,
        definition,
        best.name,
        x,
        # pyrefly: ignore [bad-argument-type]
        y,
        numeric_features,
        categorical_features,
        text_features,
        text_language,
    )

    # Refit the selected model on the full dataset for the final artifact.
    best.pipeline.fit(x, y)
    explainer = build_explainer(best.pipeline, x)
    feature_importance = global_shap_importance(
        best.pipeline, explainer, x, definition.dataset.feature_columns, text_features=text_features
    )
    term_importance = text_term_importance(best.pipeline, explainer, x, text_features)

    checksums: dict[str, str] = {}

    pipeline_bytes = _dump(best.pipeline)
    store.put(config.ORG_ID, MODEL_PIPELINE_KEY, pipeline_bytes)
    checksums["pipeline"] = artifact_checksum(pipeline_bytes)

    explainer_bytes = _dump(explainer)
    store.put(config.ORG_ID, MODEL_EXPLAINER_KEY, explainer_bytes)
    checksums["explainer"] = artifact_checksum(explainer_bytes)

    if out_of_fold is not None:
        out_of_fold_bytes = json.dumps(out_of_fold).encode()
        store.put(config.ORG_ID, MODEL_OUT_OF_FOLD_KEY, out_of_fold_bytes)
        checksums["out_of_fold"] = artifact_checksum(out_of_fold_bytes)

    has_intervals = definition.model.task_type == "regression"
    if has_intervals:
        pipeline_lower, pipeline_upper = fit_quantile_pipelines(
            numeric_features,
            categorical_features,
            x,
            # pyrefly: ignore [bad-argument-type]
            y,
            text_features,
            text_language,
        )
        lower_bytes = _dump(pipeline_lower)
        store.put(config.ORG_ID, MODEL_PIPELINE_LOWER_KEY, lower_bytes)
        checksums["pipeline_lower"] = artifact_checksum(lower_bytes)

        upper_bytes = _dump(pipeline_upper)
        store.put(config.ORG_ID, MODEL_PIPELINE_UPPER_KEY, upper_bytes)
        checksums["pipeline_upper"] = artifact_checksum(upper_bytes)
        logger.success("90% prediction interval models trained for scenario={} org={}", slug, config.ORG_ID)

    challenger = _train_challenger(
        config,
        store,
        definition,
        x,
        # pyrefly: ignore [bad-argument-type]
        y,
        x_train.index,
        x_test.index,
        numeric_features,
        categorical_features,
        text_features,
        selection_metric,
    )
    if challenger is not None:
        challenger_pipeline_bytes = _dump(challenger.pipeline)
        store.put(config.ORG_ID, MODEL_CHALLENGER_PIPELINE_KEY, challenger_pipeline_bytes)
        checksums["challenger_pipeline"] = artifact_checksum(challenger_pipeline_bytes)
        challenger_explainer_bytes = _dump(challenger.explainer)
        store.put(config.ORG_ID, MODEL_CHALLENGER_EXPLAINER_KEY, challenger_explainer_bytes)
        checksums["challenger_explainer"] = artifact_checksum(challenger_explainer_bytes)
        gain = challenger.metadata["selection_score"] - best.selection_score
        challenger.metadata["gain_over_deployed"] = round(gain, 4)
        # Reported, never auto-promoted (see scenario_schema.TextChallenger).
        challenger.metadata["would_be_adopted"] = gain > model.accuracy_gain_threshold_for_complexity
        if challenger.metadata["would_be_adopted"]:
            logger.warning(
                "Text challenger beats the deployed model by {:.4f} (> {:.4f}) — consider promoting it",
                gain,
                model.accuracy_gain_threshold_for_complexity,
            )

    # Written last, once every artifact above is confirmed uploaded — prediction's
    # model_cache treats this as the manifest: it won't serve an artifact whose bytes
    # don't match the checksum recorded here (see MODEL_CHECKSUMS_METADATA_FIELD).
    metadata = {
        "scenario_slug": slug,
        "model_name": best.name,
        "test_score": best.test_score,
        "task_type": definition.model.task_type,
        "candidates_evaluated": [c.name for c in candidates],
        # Model card (served by prediction's GET /model/{slug}/card): how the model was
        # chosen and how it scores on data it never saw.
        "selection_metric": selection_metric,
        "cv_folds": model.cv_folds,
        "accuracy_gain_threshold_for_complexity": model.accuracy_gain_threshold_for_complexity,
        "candidate_scores": [
            {"name": c.name, "selection_score": c.selection_score, "metrics": c.metrics} for c in candidates
        ],
        "metrics": best.metrics,
        "holdout_evaluation": evaluation,
        # Rows a blocking business rule stops are never trained on (the model must not
        # re-learn what the rules decide) — see _drop_rule_stopped_rows.
        "rules_excluded_rows": rules_excluded_rows,
        "text_language": text_language,
        "training_rows": len(x_train),
        "holdout_rows": len(x_test),
        "feature_columns": definition.dataset.feature_columns,
        "transformed_feature_names": transformed_feature_names(best.pipeline),
        "global_feature_importance": feature_importance,
        # Free-text features: which vocabulary pushes predictions up/down (see
        # text_term_importance) — and which columns prediction must treat as text.
        "text_columns": text_features,
        "text_term_importance": term_importance,
        # The sentence-embedding challenger's model card (None when not configured or
        # not trainable this run — see _train_challenger).
        "challenger": challenger.metadata if challenger is not None else None,
        # Cross-fitted scores of every row (MODEL_OUT_OF_FOLD_KEY) — their summary only.
        "out_of_fold": (
            {k: out_of_fold[k] for k in ("model_name", "folds", "roc_auc")} | {"rows": len(out_of_fold["rows"])}
            if out_of_fold is not None
            else None
        ),
        "target": definition.dataset.target,
        "has_intervals": has_intervals,
        MODEL_CHECKSUMS_METADATA_FIELD: checksums,
    }
    store.put(config.ORG_ID, MODEL_METADATA_KEY, json.dumps(metadata, indent=2).encode())

    # Observability mirror only (MLOps monitor) — runs after every artifact is safely
    # in SeaweedFS and can never fail the job; see training.core.mlflow_tracking.
    log_training_run(
        config.MLFLOW_TRACKING_URI,
        org_id=config.ORG_ID,
        scenario_slug=slug,
        candidates=candidates,
        selected=best,
        metadata=metadata,
        dataset_rows=len(df),
        accuracy_gain_threshold=definition.model.accuracy_gain_threshold_for_complexity,
    )

    logger.success("training finished for scenario={} org={}", slug, config.ORG_ID)


def _drop_rule_stopped_rows(df: pd.DataFrame, definition: ScenarioDefinition) -> tuple[pd.DataFrame, int]:
    """Without `business_rules`, `df` unchanged. With them, only the rows that pass every
    blocking rule: the rest (a missing document, a value out of range, a requirement not
    met) are decided by the rules, so fitting on them would teach the model the rules'
    own outcome instead of the judgement left to it. Returns (rows kept, rows dropped).
    """
    assert definition.dataset is not None
    rules = definition.dataset.business_rules
    if rules is None:
        return df, 0
    records = df.astype(object).where(df.notna(), None).to_dict("records")
    keep = pd.Series([passes(rules, r) for r in records], index=df.index)
    dropped = int((~keep).sum())
    logger.info("Business rules stop {} of {} rows — training on the other {}", dropped, len(df), int(keep.sum()))
    kept: pd.DataFrame = df.loc[keep]
    return kept, dropped


def _out_of_fold(
    slug: str,
    definition: ScenarioDefinition,
    model_name: str,
    x: pd.DataFrame,
    y: pd.Series,
    numeric_features: list[str],
    categorical_features: list[str],
    text_features: list[str],
    text_language: str = "en",
) -> dict[str, Any] | None:
    """`model.out_of_fold_scores`' cross-fitted scores, or None when not configured —
    or not possible (too many rows, or a class smaller than the number of folds).
    """
    assert definition.model is not None
    if not definition.model.out_of_fold_scores:
        return None
    folds = definition.model.cv_folds or 5
    if len(x) > MAX_OUT_OF_FOLD_ROWS or int(y.value_counts().min()) < folds:
        logger.warning(
            "Skipping out-of-fold scores for scenario={}: {} rows (max {}), smallest class {} (< {} folds?)",
            slug,
            len(x),
            MAX_OUT_OF_FOLD_ROWS,
            int(y.value_counts().min()),
            folds,
        )
        return None
    explain = len(x) <= MAX_OUT_OF_FOLD_EXPLAINED_ROWS
    scores = out_of_fold_scores(
        model_name,
        x,
        y,
        numeric_features,
        categorical_features,
        folds,
        text_features,
        explain=explain,
        text_language=text_language,
    )
    logger.success(
        "Out-of-fold scores for scenario={}: {} rows, ROC AUC {}{}",
        slug,
        len(x),
        scores["roc_auc"],
        "" if explain else " (probabilities only)",
    )
    return scores


def _train_challenger(
    config: EnvConfig,
    store: ObjectStore,
    definition: ScenarioDefinition,
    x: pd.DataFrame,
    y: pd.Series,
    train_index: pd.Index,
    test_index: pd.Index,
    numeric_features: list[str],
    categorical_features: list[str],
    text_features: list[str],
    selection_metric: str,
) -> ChallengerResult | None:
    """The scenario's sentence-embedding challenger, or None. Optional by design: a
    missing embedding cache or an unreachable gateway is logged, never a failed job —
    the deployed (champion) model's artifacts are already safely written.
    """
    assert definition.dataset is not None and definition.model is not None
    challenger = definition.model.text_challenger
    if challenger is None or not text_features:
        return None
    provider = None
    if config.LLM_GATEWAY_URL and config.LLM_GATEWAY_API_KEY:
        provider = GatewayEmbeddingProvider(
            config.LLM_GATEWAY_URL,
            config.LLM_GATEWAY_API_KEY.get_secret_value(),
            challenger.embedding_model,
            probe=False,
            timeout=120.0,
        )
    try:
        return train_text_challenger(
            challenger,
            store=store,
            org_id=config.ORG_ID,
            provider=provider,
            x=x,
            y=y,
            train_index=train_index,
            test_index=test_index,
            numeric_features=numeric_features,
            categorical_features=categorical_features,
            text_features=text_features,
            feature_columns=definition.dataset.feature_columns,
            task_type=definition.model.task_type,
            selection_metric=selection_metric,
            cv_folds=definition.model.cv_folds,
        )
    except ChallengerUnavailableError as exc:
        logger.warning("Text challenger skipped for scenario={}: {}", definition.slug, exc)
    except Exception:
        logger.exception("Text challenger failed for scenario={} — the deployed model is unaffected", definition.slug)
    return None


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
