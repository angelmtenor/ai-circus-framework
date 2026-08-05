"""Tests for predict(), against a small real sklearn pipeline + SHAP explainer."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import shap
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from prediction.core.model_cache import ModelArtifacts
from prediction.core.predict import predict


@pytest.fixture
def artifacts() -> ModelArtifacts:
    """A tiny real pipeline+TreeExplainer trained on synthetic data, plus matching metadata."""
    rng = np.random.default_rng(0)
    n = 60
    df = pd.DataFrame({
        "numeric_feature": rng.normal(size=n),
        "category_feature": rng.choice(["A", "B"], size=n),
    })
    target = (df["numeric_feature"] > 0).astype(int)

    preprocessor = ColumnTransformer([
        ("num", StandardScaler(), ["numeric_feature"]),
        ("cat", OneHotEncoder(handle_unknown="ignore"), ["category_feature"]),
    ])
    pipeline = Pipeline([("preprocessor", preprocessor), ("model", RandomForestClassifier(random_state=0))])
    pipeline.fit(df, target)

    x_transformed = pipeline.named_steps["preprocessor"].transform(df)
    explainer = shap.TreeExplainer(
        pipeline.named_steps["model"],
        data=x_transformed,
        feature_perturbation="interventional",
        model_output="probability",
    )

    metadata = {
        "feature_columns": ["numeric_feature", "category_feature"],
        "transformed_feature_names": list(pipeline.named_steps["preprocessor"].get_feature_names_out()),
    }
    return ModelArtifacts(pipeline=pipeline, explainer=explainer, metadata=metadata)


def test_predict_returns_one_result_per_record(artifacts: ModelArtifacts) -> None:
    """predict() returns exactly one probability+contributions entry per input record."""
    records = pd.DataFrame([
        {"numeric_feature": 1.5, "category_feature": "A"},
        {"numeric_feature": -1.5, "category_feature": "B"},
    ])

    results = predict(artifacts, records)

    assert len(results) == len(records)
    for result in results:
        assert 0.0 <= result.probability <= 1.0
        assert set(result.contributions) == set(artifacts.metadata["transformed_feature_names"])


def test_predict_ignores_extra_columns_not_in_feature_columns(artifacts: ModelArtifacts) -> None:
    """Extra columns in the request (e.g. an id column) are ignored, not passed to the model."""
    records = pd.DataFrame([{"numeric_feature": 0.5, "category_feature": "A", "customer_id": "cust-1"}])

    results = predict(artifacts, records)

    assert len(results) == 1
