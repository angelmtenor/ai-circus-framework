"""Tests for model training, Green Code candidate selection, and SHAP explainability."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression

from training.core.training import (
    build_explainer,
    build_pipeline,
    select_best_candidate,
    split_features,
    train_candidate,
    transformed_feature_names,
)


@pytest.fixture
def synthetic_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    """A small, deterministic, linearly-separable synthetic classification dataset."""
    rng = np.random.default_rng(0)
    n = 200
    numeric = rng.normal(size=n)
    category = rng.choice(["A", "B"], size=n)
    # Target correlates with `numeric` so both candidates can learn something real.
    target = (numeric + rng.normal(scale=0.1, size=n) > 0).astype(int)

    df = pd.DataFrame({"numeric_feature": numeric, "category_feature": category, "target": target})
    x = df[["numeric_feature", "category_feature"]]
    y = df["target"]
    split = n * 4 // 5
    return x.iloc[:split], x.iloc[split:], y.iloc[:split], y.iloc[split:]


@pytest.fixture
def synthetic_regression_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    """A small, deterministic, linearly-correlated synthetic regression dataset."""
    rng = np.random.default_rng(0)
    n = 200
    numeric = rng.normal(size=n)
    category = rng.choice(["A", "B"], size=n)
    target = numeric * 2 + rng.normal(scale=0.1, size=n)

    df = pd.DataFrame({"numeric_feature": numeric, "category_feature": category, "target": target})
    x = df[["numeric_feature", "category_feature"]]
    y = df["target"]
    split = n * 4 // 5
    return x.iloc[:split], x.iloc[split:], y.iloc[:split], y.iloc[split:]


def test_split_features_separates_by_dtype() -> None:
    """Numeric-dtype columns and category-dtype columns are split correctly."""
    df = pd.DataFrame({"a": [1, 2], "b": pd.Categorical(["x", "y"])})

    numeric, categorical = split_features(df, ["a", "b"])

    assert numeric == ["a"]
    assert categorical == ["b"]


def test_build_pipeline_fits_and_predicts(synthetic_data: tuple) -> None:
    """A built pipeline can be fit and produce predictions on held-out data."""
    x_train, x_test, y_train, _y_test = synthetic_data

    pipeline = build_pipeline(["numeric_feature"], ["category_feature"], LogisticRegression())
    pipeline.fit(x_train, y_train)

    predictions = pipeline.predict(x_test)

    assert len(predictions) == len(x_test)


def test_train_candidate_scores_on_test_set(synthetic_data: tuple) -> None:
    """train_candidate fits the named estimator and reports its held-out accuracy."""
    x_train, x_test, y_train, y_test = synthetic_data

    candidate = train_candidate(
        "logistic_regression", x_train, y_train, x_test, y_test, ["numeric_feature"], ["category_feature"]
    )

    assert candidate.name == "logistic_regression"
    assert 0.0 <= candidate.test_score <= 1.0


def test_train_candidate_scores_regression_on_test_set(synthetic_regression_data: tuple) -> None:
    """train_candidate fits a regression estimator and reports its held-out R²."""
    x_train, x_test, y_train, y_test = synthetic_regression_data

    candidate = train_candidate(
        "linear_regression",
        x_train,
        y_train,
        x_test,
        y_test,
        ["numeric_feature"],
        ["category_feature"],
        task_type="regression",
    )

    assert candidate.name == "linear_regression"
    assert candidate.test_score > 0.9  # near-perfect linear signal


class _FakeCandidate:
    """Minimal stand-in for TrainedCandidate, avoiding a real sklearn fit in selection tests."""

    def __init__(self, name: str, test_score: float) -> None:
        """Store the fields select_best_candidate() reads."""
        self.name = name
        self.test_score = test_score


def test_select_best_candidate_keeps_simpler_model_below_threshold() -> None:
    """A more complex candidate that only marginally beats the simpler one is rejected (Green Code)."""
    candidates = [_FakeCandidate("logistic_regression", 0.80), _FakeCandidate("random_forest", 0.81)]

    best = select_best_candidate(candidates, accuracy_gain_threshold=0.02)

    assert best.name == "logistic_regression"


def test_select_best_candidate_adopts_significantly_better_model() -> None:
    """A more complex candidate that clearly beats the simpler one is adopted."""
    candidates = [_FakeCandidate("logistic_regression", 0.80), _FakeCandidate("random_forest", 0.90)]

    best = select_best_candidate(candidates, accuracy_gain_threshold=0.02)

    assert best.name == "random_forest"


def test_build_explainer_uses_tree_explainer_for_random_forest(synthetic_data: tuple) -> None:
    """A random_forest pipeline gets a TreeExplainer (Green Code: exact, no sampling needed)."""
    x_train, _x_test, y_train, _y_test = synthetic_data
    pipeline = build_pipeline(
        ["numeric_feature"], ["category_feature"], RandomForestClassifier(n_estimators=10, random_state=0)
    )
    pipeline.fit(x_train, y_train)

    explainer = build_explainer(pipeline, x_train)

    shap_values = explainer(pipeline.named_steps["preprocessor"].transform(x_train))
    assert shap_values.values.shape[0] == len(x_train)


def test_build_explainer_uses_linear_explainer_for_logistic_regression(synthetic_data: tuple) -> None:
    """A logistic_regression pipeline gets a LinearExplainer."""
    x_train, _x_test, y_train, _y_test = synthetic_data
    pipeline = build_pipeline(["numeric_feature"], ["category_feature"], LogisticRegression())
    pipeline.fit(x_train, y_train)

    explainer = build_explainer(pipeline, x_train)

    shap_values = explainer.shap_values(pipeline.named_steps["preprocessor"].transform(x_train))
    assert shap_values.shape[0] == len(x_train)


def test_build_explainer_uses_tree_explainer_for_random_forest_regressor(synthetic_regression_data: tuple) -> None:
    """A random_forest regressor pipeline gets a plain (non-probability) TreeExplainer."""
    x_train, _x_test, y_train, _y_test = synthetic_regression_data
    pipeline = build_pipeline(
        ["numeric_feature"], ["category_feature"], RandomForestRegressor(n_estimators=10, random_state=0)
    )
    pipeline.fit(x_train, y_train)

    explainer = build_explainer(pipeline, x_train)

    shap_values = explainer.shap_values(pipeline.named_steps["preprocessor"].transform(x_train))
    assert shap_values.shape == (len(x_train), pipeline.named_steps["preprocessor"].transform(x_train).shape[1])


def test_build_explainer_uses_linear_explainer_for_linear_regression(synthetic_regression_data: tuple) -> None:
    """A linear_regression pipeline gets a LinearExplainer."""
    x_train, _x_test, y_train, _y_test = synthetic_regression_data
    pipeline = build_pipeline(["numeric_feature"], ["category_feature"], LinearRegression())
    pipeline.fit(x_train, y_train)

    explainer = build_explainer(pipeline, x_train)

    shap_values = explainer.shap_values(pipeline.named_steps["preprocessor"].transform(x_train))
    assert shap_values.shape[0] == len(x_train)


def test_transformed_feature_names_include_one_hot_columns(synthetic_data: tuple) -> None:
    """transformed_feature_names reflects the one-hot-encoded categorical column."""
    x_train, _x_test, y_train, _y_test = synthetic_data
    pipeline = build_pipeline(["numeric_feature"], ["category_feature"], LogisticRegression())
    pipeline.fit(x_train, y_train)

    names = transformed_feature_names(pipeline)

    assert any("category_feature" in name for name in names)
    assert any("numeric_feature" in name for name in names)
