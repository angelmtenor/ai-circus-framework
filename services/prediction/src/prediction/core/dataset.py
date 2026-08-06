"""
- Title:    Dataset sampling + held-out evaluation for tabular_ml scenarios
- Author:   ai-circus-framework contributors

Reads the same normalized parquet training already wrote to MinIO — real rows, not
fabricated ones. The evaluation reproduces training's exact held-out split
(train_test_split(random_state=0, test_size=0.2), stratified for classification) to
score the deployed pipeline on data it wasn't fit on; note the deployed pipeline was
then refit on the *full* dataset for the final artifact (see training/app.py), so this
is a reference evaluation of the selection process, not a strict leakage-free score of
the exact deployed weights.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import NORMALIZED_DATASET_KEY
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
    root_mean_squared_error,
)
from sklearn.model_selection import train_test_split

from prediction.core.model_cache import ModelArtifacts

TEST_SIZE = 0.2
SPLIT_RANDOM_STATE = 0


def load_normalized(store: ObjectStore, org_id: str) -> pd.DataFrame:
    """Load the tenant's cleaned (not yet one-hot-encoded) dataset."""
    return pd.read_parquet(io.BytesIO(store.get(org_id, NORMALIZED_DATASET_KEY)))


@dataclass(frozen=True)
class DatasetSample:
    """A row-limited, JSON-friendly slice of a tenant's dataset."""

    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int


def sample_rows(df: pd.DataFrame, columns: list[str], limit: int) -> DatasetSample:
    """Return up to `limit` evenly-spaced rows (so a small sample still spans the
    whole dataset rather than just its head) restricted to `columns`.
    """
    total_rows = len(df)
    if total_rows <= limit:
        sampled = df
    else:
        idx = np.linspace(0, total_rows - 1, limit, dtype=int)
        sampled = df.iloc[idx]
    subset = sampled.loc[:, columns]
    rows = [
        {k: (v.item() if isinstance(v, np.generic) else v) for k, v in row.items()}
        for row in subset.to_dict(orient="records")
    ]
    return DatasetSample(columns=columns, rows=rows, total_rows=total_rows)


def _feature_importance(artifacts: ModelArtifacts) -> list[dict[str, Any]]:
    """Aggregate the fitted estimator's importances from transformed (one-hot) names
    back to the original feature they came from, ranked descending.
    """
    model = artifacts.pipeline.named_steps["model"]
    names: list[str] = artifacts.metadata["transformed_feature_names"]
    feature_columns: list[str] = artifacts.metadata["feature_columns"]

    if hasattr(model, "feature_importances_"):
        raw = np.abs(np.asarray(model.feature_importances_, dtype=float))
    elif hasattr(model, "coef_"):
        raw = np.abs(np.asarray(model.coef_, dtype=float)).reshape(-1)
    else:
        return []

    totals: dict[str, float] = {}
    for name, value in zip(names, raw, strict=True):
        unprefixed = name.split("__", 1)[-1]
        original = next((f for f in feature_columns if unprefixed.startswith(f)), unprefixed)
        totals[original] = totals.get(original, 0.0) + float(value)

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [{"feature": name, "importance": round(value, 4)} for name, value in ranked]


@dataclass(frozen=True)
class EvaluationResult:
    """A held-out evaluation of one tenant's deployed pipeline, ready to render as a
    metrics/feature-importance/predicted-vs-actual dashboard.
    """

    task_type: str
    target: str
    n: int
    metrics: dict[str, float]
    feature_importance: list[dict[str, Any]]
    breakdown_feature: str | None
    breakdown: list[dict[str, Any]]
    actuals: list[float]
    predictions: list[float]
    prediction_lower: list[float] | None
    prediction_upper: list[float] | None


def evaluate(artifacts: ModelArtifacts, df: pd.DataFrame, limit: int) -> EvaluationResult:
    """Reproduce training's held-out split and score the deployed pipeline on it."""
    feature_columns: list[str] = artifacts.metadata["feature_columns"]
    target: str = artifacts.metadata["target"]
    task_type: str = artifacts.metadata["task_type"]

    x = df.loc[:, feature_columns]
    y = df[target]
    stratify = y if task_type == "classification" else None
    _, x_test, _, y_test = train_test_split(
        x, y, test_size=TEST_SIZE, random_state=SPLIT_RANDOM_STATE, stratify=stratify
    )

    if len(x_test) > limit:
        idx = np.linspace(0, len(x_test) - 1, limit, dtype=int)
        x_test = x_test.iloc[idx]
        y_test = y_test.iloc[idx]

    lower: np.ndarray | None = None
    upper: np.ndarray | None = None
    predicted_class: np.ndarray | None = None

    if task_type == "regression":
        predictions = np.asarray(artifacts.pipeline.predict(x_test), dtype=float)
        metrics = {
            "mae": round(float(mean_absolute_error(y_test, predictions)), 4),
            "rmse": round(float(root_mean_squared_error(y_test, predictions)), 4),
            "r2": round(float(r2_score(y_test, predictions)), 4),
        }
        if artifacts.pipeline_lower is not None and artifacts.pipeline_upper is not None:
            lower = np.asarray(artifacts.pipeline_lower.predict(x_test), dtype=float)
            upper = np.asarray(artifacts.pipeline_upper.predict(x_test), dtype=float)
    else:
        predictions = np.asarray(artifacts.pipeline.predict_proba(x_test))[:, 1]
        predicted_class = (predictions >= 0.5).astype(int)
        metrics = {
            "accuracy": round(float(accuracy_score(y_test, predicted_class)), 4),
            "precision": round(float(precision_score(y_test, predicted_class, zero_division=0)), 4),
            "recall": round(float(recall_score(y_test, predicted_class, zero_division=0)), 4),
            "f1": round(float(f1_score(y_test, predicted_class, zero_division=0)), 4),
            "roc_auc": round(float(roc_auc_score(y_test, predictions)), 4),
        }

    categorical_features = [c for c in feature_columns if not pd.api.types.is_numeric_dtype(x_test[c])]
    breakdown_feature = categorical_features[0] if categorical_features else None
    breakdown: list[dict[str, Any]] = []
    if breakdown_feature is not None:
        y_test_arr = y_test.to_numpy(dtype=float)
        per_row_score = (
            np.abs(predictions - y_test_arr) if task_type == "regression" else (predicted_class == y_test_arr).astype(float)
        )
        group_df = pd.DataFrame({breakdown_feature: x_test[breakdown_feature].to_numpy(), "score": per_row_score})
        for category, group in group_df.groupby(breakdown_feature):
            breakdown.append({"category": str(category), "score": round(float(group["score"].mean()), 4), "n": int(len(group))})
        breakdown.sort(key=lambda b: b["n"], reverse=True)

    return EvaluationResult(
        task_type=task_type,
        target=target,
        n=len(x_test),
        metrics=metrics,
        feature_importance=_feature_importance(artifacts),
        breakdown_feature=breakdown_feature,
        breakdown=breakdown,
        actuals=[round(float(v), 4) for v in y_test.to_numpy(dtype=float)],
        predictions=[round(float(v), 4) for v in predictions],
        prediction_lower=[round(float(v), 4) for v in lower] if lower is not None else None,
        prediction_upper=[round(float(v), 4) for v in upper] if upper is not None else None,
    )
