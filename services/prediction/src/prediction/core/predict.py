"""
- Title:    Prediction + SHAP contribution computation
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from prediction.core.model_cache import ModelArtifacts


@dataclass(frozen=True)
class PredictionResult:
    """One record's churn probability and per-(transformed)-feature SHAP contributions."""

    probability: float
    contributions: dict[str, float]


def predict(artifacts: ModelArtifacts, records: pd.DataFrame) -> list[PredictionResult]:
    """Return probability + per-(transformed)-feature SHAP contributions for each record.

    Contributions are keyed by *transformed* feature names (post one-hot-encoding) —
    e.g. `cat__Geography_France`, not `Geography` — since that's the level at which
    the explainer computed them.
    """
    feature_columns = artifacts.metadata["feature_columns"]
    x = records.loc[:, feature_columns]

    probabilities = np.asarray(artifacts.pipeline.predict_proba(x))[:, 1]
    x_transformed = artifacts.pipeline.named_steps["preprocessor"].transform(x)
    shap_values = np.asarray(artifacts.explainer.shap_values(x_transformed))
    # Binary-classification TreeExplainer with model_output="probability" returns
    # (n_samples, n_features, n_classes); keep just the positive class's contributions.
    if shap_values.ndim == 3:
        shap_values = shap_values[:, :, 1]
    feature_names = artifacts.metadata["transformed_feature_names"]

    results = []
    for i, probability in enumerate(probabilities):
        contributions = dict(zip(feature_names, [round(float(v), 4) for v in shap_values[i]], strict=True))
        results.append(PredictionResult(probability=round(float(probability), 4), contributions=contributions))
    return results
