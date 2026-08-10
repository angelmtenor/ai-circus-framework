"""
- Title:    Prediction + SHAP contribution computation
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from prediction.core.model_cache import ModelArtifacts


class MissingFeatureColumnsError(ValueError):
    """Raised when a request record is missing one or more of the scenario's
    feature_columns — the api layer turns this into a 422, not a raw 500.
    """

    def __init__(self, missing_columns: list[str]) -> None:
        """Store the missing column names for the caller to report back verbatim."""
        self.missing_columns = missing_columns
        super().__init__(f"Record(s) missing required feature column(s): {', '.join(missing_columns)}")


@dataclass(frozen=True)
class PredictionResult:
    """One record's prediction (probability for classification, raw value for
    regression) and per-(transformed)-feature SHAP contributions.

    `prediction_lower`/`prediction_upper` bound a 90% prediction interval — only set
    for regression scenarios whose artifacts include the quantile pipelines.
    """

    prediction: float
    contributions: dict[str, float]
    prediction_lower: float | None = None
    prediction_upper: float | None = None


def predict(artifacts: ModelArtifacts, records: pd.DataFrame) -> list[PredictionResult]:
    """Return prediction + per-(transformed)-feature SHAP contributions for each record.

    Contributions are keyed by *transformed* feature names (post one-hot-encoding) —
    e.g. `cat__Geography_France`, not `Geography` — since that's the level at which
    the explainer computed them.
    """
    feature_columns = artifacts.metadata["feature_columns"]
    missing = [c for c in feature_columns if c not in records.columns]
    if missing:
        raise MissingFeatureColumnsError(missing)
    x = records.loc[:, feature_columns]

    if artifacts.metadata["task_type"] == "regression":
        predictions = np.asarray(artifacts.pipeline.predict(x))
    else:
        predictions = np.asarray(artifacts.pipeline.predict_proba(x))[:, 1]
    x_transformed = artifacts.pipeline.named_steps["preprocessor"].transform(x)
    shap_values = np.asarray(artifacts.explainer.shap_values(x_transformed))
    # Binary-classification TreeExplainer with model_output="probability" returns
    # (n_samples, n_features, n_classes); keep just the positive class's contributions.
    # (Regression explainers return a plain 2D (n_samples, n_features) array, so this
    # branch never triggers for them.)
    if shap_values.ndim == 3:
        shap_values = shap_values[:, :, 1]
    feature_names = artifacts.metadata["transformed_feature_names"]

    lower = artifacts.pipeline_lower.predict(x) if artifacts.pipeline_lower is not None else None
    upper = artifacts.pipeline_upper.predict(x) if artifacts.pipeline_upper is not None else None

    results = []
    for i, prediction in enumerate(predictions):
        contributions = dict(zip(feature_names, [round(float(v), 4) for v in shap_values[i]], strict=True))
        results.append(
            PredictionResult(
                prediction=round(float(prediction), 4),
                contributions=contributions,
                prediction_lower=round(float(lower[i]), 4) if lower is not None else None,
                prediction_upper=round(float(upper[i]), 4) if upper is not None else None,
            )
        )
    return results
