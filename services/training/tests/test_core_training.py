"""Tests for model training, Green Code candidate selection, and SHAP explainability."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression

from training.core.training import (
    _aggregate_by_feature,
    build_explainer,
    build_pipeline,
    global_shap_importance,
    holdout_evaluation,
    select_best_candidate,
    split_features,
    text_term_importance,
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


def test_split_features_treats_boolean_columns_as_categorical() -> None:
    """A bool-dtype column (e.g. is_holiday) is classified categorical, not numeric.

    pandas' is_numeric_dtype() returns True for bool columns; without an explicit
    exclusion, a boolean feature would be routed through the numeric imputer/scaler
    instead of one-hot encoding, breaking scenarios like energy_building.
    """
    df = pd.DataFrame({"a": [1, 2], "b": pd.Categorical(["x", "y"]), "is_holiday": [True, False]})

    numeric, categorical = split_features(df, ["a", "b", "is_holiday"])

    assert numeric == ["a"]
    assert categorical == ["b", "is_holiday"]


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


def test_train_candidate_scores_lightgbm_classifier_on_test_set(synthetic_data: tuple) -> None:
    """train_candidate fits the lightgbm classifier estimator and reports its held-out accuracy."""
    x_train, x_test, y_train, y_test = synthetic_data

    candidate = train_candidate("lightgbm", x_train, y_train, x_test, y_test, ["numeric_feature"], ["category_feature"])

    assert candidate.name == "lightgbm"
    assert 0.0 <= candidate.test_score <= 1.0


class _FakeCandidate:
    """Minimal stand-in for TrainedCandidate, avoiding a real sklearn fit in selection tests."""

    def __init__(self, name: str, selection_score: float) -> None:
        """Store the fields select_best_candidate() reads."""
        self.name = name
        self.selection_score = selection_score


def test_select_best_candidate_keeps_simpler_model_below_threshold() -> None:
    """A more complex candidate that only marginally beats the simpler one is rejected (Green Code)."""
    candidates = [_FakeCandidate("logistic_regression", 0.80), _FakeCandidate("lightgbm", 0.81)]

    best = select_best_candidate(candidates, accuracy_gain_threshold=0.02)

    assert best.name == "logistic_regression"


def test_select_best_candidate_adopts_significantly_better_model() -> None:
    """A more complex candidate that clearly beats the simpler one is adopted."""
    candidates = [_FakeCandidate("logistic_regression", 0.80), _FakeCandidate("lightgbm", 0.90)]

    best = select_best_candidate(candidates, accuracy_gain_threshold=0.02)

    assert best.name == "lightgbm"


def test_build_explainer_uses_tree_explainer_for_lightgbm_classifier(synthetic_data: tuple) -> None:
    """A lightgbm classifier pipeline gets a probability TreeExplainer (Green Code: exact, no sampling needed)."""
    x_train, _x_test, y_train, _y_test = synthetic_data
    pipeline = build_pipeline(
        ["numeric_feature"], ["category_feature"], LGBMClassifier(n_estimators=10, random_state=0, verbosity=-1)
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


def test_build_explainer_uses_tree_explainer_for_lightgbm_regressor(synthetic_regression_data: tuple) -> None:
    """A lightgbm regressor pipeline gets a plain (non-probability) TreeExplainer."""
    x_train, _x_test, y_train, _y_test = synthetic_regression_data
    pipeline = build_pipeline(
        ["numeric_feature"], ["category_feature"], LGBMRegressor(n_estimators=10, random_state=0, verbosity=-1)
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


def test_aggregate_by_feature_sums_one_hot_columns_back_to_original() -> None:
    """Multiple one-hot columns from the same original feature are summed, not kept separate."""
    names = ["num__numeric_feature", "cat__category_feature_A", "cat__category_feature_B"]
    values = np.array([0.5, 0.1, 0.2])

    ranked = _aggregate_by_feature(names, values, ["numeric_feature", "category_feature"])

    assert ranked[0] == {"feature": "numeric_feature", "importance": 0.5}
    assert ranked[1] == {"feature": "category_feature", "importance": pytest.approx(0.3)}


def test_global_shap_importance_ranks_features_for_classifier(synthetic_data: tuple) -> None:
    """Global SHAP importance covers every original feature, ranked descending, for a real fitted pipeline."""
    x_train, _x_test, y_train, _y_test = synthetic_data
    pipeline = build_pipeline(
        ["numeric_feature"], ["category_feature"], LGBMClassifier(n_estimators=10, random_state=0, verbosity=-1)
    )
    pipeline.fit(x_train, y_train)
    explainer = build_explainer(pipeline, x_train)

    result = global_shap_importance(pipeline, explainer, x_train, ["numeric_feature", "category_feature"])

    assert {item["feature"] for item in result} == {"numeric_feature", "category_feature"}
    importances = [item["importance"] for item in result]
    assert importances == sorted(importances, reverse=True)
    # numeric_feature is the only signal correlated with the synthetic target — it should dominate.
    assert result[0]["feature"] == "numeric_feature"


def test_global_shap_importance_respects_sample_size_cap(synthetic_data: tuple) -> None:
    """A sample_size smaller than the dataset doesn't error and still ranks every feature."""
    x_train, _x_test, y_train, _y_test = synthetic_data
    pipeline = build_pipeline(
        ["numeric_feature"], ["category_feature"], LGBMClassifier(n_estimators=10, random_state=0, verbosity=-1)
    )
    pipeline.fit(x_train, y_train)
    explainer = build_explainer(pipeline, x_train)

    result = global_shap_importance(
        pipeline, explainer, x_train, ["numeric_feature", "category_feature"], sample_size=10
    )

    assert {item["feature"] for item in result} == {"numeric_feature", "category_feature"}


def test_transformed_feature_names_include_one_hot_columns(synthetic_data: tuple) -> None:
    """transformed_feature_names reflects the one-hot-encoded categorical column."""
    x_train, _x_test, y_train, _y_test = synthetic_data
    pipeline = build_pipeline(["numeric_feature"], ["category_feature"], LogisticRegression())
    pipeline.fit(x_train, y_train)

    names = transformed_feature_names(pipeline)

    assert any("category_feature" in name for name in names)
    assert any("numeric_feature" in name for name in names)


def test_train_candidate_reports_holdout_accuracy_and_roc_auc(synthetic_data: tuple) -> None:
    """Without CV, a classifier is ranked on its hold-out accuracy (the default) and
    also reports hold-out ROC AUC.
    """
    x_train, x_test, y_train, y_test = synthetic_data
    candidate = train_candidate(
        "logistic_regression", x_train, y_train, x_test, y_test, ["numeric_feature"], ["category_feature"]
    )
    assert set(candidate.metrics) == {"holdout_accuracy", "holdout_roc_auc"}
    assert candidate.selection_score == candidate.metrics["holdout_accuracy"]
    assert candidate.metrics["holdout_roc_auc"] > 0.9


def test_train_candidate_ranks_on_cross_validated_selection_metric(synthetic_data: tuple) -> None:
    """With cv_folds, the selection score is the CV mean of the chosen metric, and the
    hold-out is still reported alongside it.
    """
    x_train, x_test, y_train, y_test = synthetic_data
    candidate = train_candidate(
        "lightgbm_small_data",
        x_train,
        y_train,
        x_test,
        y_test,
        ["numeric_feature"],
        ["category_feature"],
        selection_metric="roc_auc",
        cv_folds=3,
    )
    assert candidate.selection_score == candidate.metrics["cv_roc_auc_mean"]
    assert {"cv_accuracy_mean", "cv_accuracy_std", "cv_roc_auc_std", "holdout_roc_auc"} <= set(candidate.metrics)
    assert 0.5 < candidate.selection_score <= 1.0


def test_train_candidate_cross_validates_regression_on_r2(synthetic_regression_data: tuple) -> None:
    """Regression CV reports (and ranks on) R²."""
    x_train, x_test, y_train, y_test = synthetic_regression_data
    candidate = train_candidate(
        "linear_regression",
        x_train,
        y_train,
        x_test,
        y_test,
        ["numeric_feature"],
        ["category_feature"],
        "regression",
        cv_folds=3,
    )
    assert candidate.selection_score == candidate.metrics["cv_r2_mean"] > 0.9
    assert "holdout_r2" in candidate.metrics


def test_holdout_evaluation_returns_a_consistent_roc_curve_and_confusion_matrix(synthetic_data: tuple) -> None:
    """The hold-out evaluation covers every hold-out row and a monotone ROC curve from (0,0) to (1,1)."""
    x_train, x_test, y_train, y_test = synthetic_data
    pipeline = build_pipeline(["numeric_feature"], ["category_feature"], LogisticRegression()).fit(x_train, y_train)

    evaluation = holdout_evaluation(pipeline, x_test, y_test)

    assert evaluation is not None
    matrix = evaluation["confusion_matrix"]
    assert sum(matrix.values()) == evaluation["n"] == len(y_test)
    assert matrix["tp"] + matrix["fn"] == int(y_test.sum())
    fpr, tpr = evaluation["roc_curve"]["fpr"], evaluation["roc_curve"]["tpr"]
    assert (fpr[0], tpr[0], fpr[-1], tpr[-1]) == (0.0, 0.0, 1.0, 1.0)
    assert fpr == sorted(fpr) and tpr == sorted(tpr)


def test_holdout_evaluation_is_none_for_a_single_class_holdout(synthetic_data: tuple) -> None:
    """No ROC curve exists when the hold-out has only one class."""
    x_train, x_test, y_train, _y_test = synthetic_data
    pipeline = build_pipeline(["numeric_feature"], ["category_feature"], LogisticRegression()).fit(x_train, y_train)
    assert holdout_evaluation(pipeline, x_test, pd.Series([1] * len(x_test), index=x_test.index)) is None


# --- free-text (`type: text`) features -------------------------------------------------

BAD_WORDS = ["politics", "micromanagement", "no direction", "blame culture", "incompetent leadership"]
GOOD_WORDS = ["supportive lead", "great mentoring", "transparent roadmap", "trust", "listens"]
NEUTRAL = ["the office is old", "parking is hard", "long commute", "canteen food", "legacy code"]


@pytest.fixture
def synthetic_text_data() -> tuple[pd.DataFrame, pd.Series]:
    """Reviews whose words carry the signal (bad-leadership words -> 1), plus a numeric
    and a categorical column — every phrase repeats in many documents (TF-IDF min_df).
    """
    rng = np.random.default_rng(0)
    n = 300
    target = rng.integers(0, 2, size=n)
    reviews = [
        f"{rng.choice(BAD_WORDS if t else GOOD_WORDS)} and {rng.choice(NEUTRAL)}. {rng.choice(NEUTRAL)}" for t in target
    ]
    x = pd.DataFrame({
        "pay": rng.integers(1, 6, size=n).astype(float),
        "family": pd.Categorical(rng.choice(["Data", "Software"], size=n)),
        "Review": reviews,
    })
    return x, pd.Series(target, name="bad")


def test_split_features_leaves_text_features_out(synthetic_text_data: tuple) -> None:
    """A text column is neither numeric nor categorical — it's named by the schema."""
    x, _y = synthetic_text_data
    assert split_features(x, list(x.columns), ["Review"]) == (["pay"], ["family"])


def test_text_pipeline_learns_words_and_rolls_shap_up_to_the_column(synthetic_text_data: tuple) -> None:
    """TF-IDF terms are prefixed by their column's step name, the model learns from the
    words, and global importance reports `Review` once (not 1 row per term).
    """
    x, y = synthetic_text_data
    pipeline = build_pipeline(["pay"], ["family"], LogisticRegression(max_iter=1000), ["Review"])
    pipeline.fit(x, y)
    names = transformed_feature_names(pipeline)
    assert "text_Review__politics" in names and "text_Review__no direction" in names
    assert pipeline.score(x, y) > 0.95

    explainer = build_explainer(pipeline, x)
    importance = global_shap_importance(pipeline, explainer, x, ["pay", "family", "Review"], text_features=["Review"])
    assert importance[0]["feature"] == "Review"
    assert {item["feature"] for item in importance} == {"pay", "family", "Review"}


def test_text_term_importance_ranks_red_and_green_flag_terms(synthetic_text_data: tuple) -> None:
    """Terms that push toward the positive class come out `positive`, and vice versa."""
    x, y = synthetic_text_data
    pipeline = build_pipeline(["pay"], ["family"], LogisticRegression(max_iter=1000), ["Review"])
    pipeline.fit(x, y)
    explainer = build_explainer(pipeline, x)

    terms = text_term_importance(pipeline, explainer, x, ["Review"], top_k=5)

    positive = {t["term"] for t in terms["Review"]["positive"]}
    negative = {t["term"] for t in terms["Review"]["negative"]}
    assert "politics" in positive and "micromanagement" in positive
    assert "trust" in negative and "listens" in negative
    assert all(t["docs"] >= 10 for t in terms["Review"]["positive"])
    assert text_term_importance(pipeline, explainer, x, []) == {}


def test_text_pipeline_trains_and_explains_with_lightgbm_and_cv(synthetic_text_data: tuple) -> None:
    """The text branch works for both candidate families, cross-validated."""
    x, y = synthetic_text_data
    candidate = train_candidate(
        "lightgbm_small_data",
        x.iloc[:240],
        y.iloc[:240],
        x.iloc[240:],
        y.iloc[240:],
        ["pay"],
        ["family"],
        "classification",
        selection_metric="roc_auc",
        cv_folds=3,
        text_features=["Review"],
    )
    assert candidate.metrics["cv_roc_auc_mean"] > 0.9
    explainer = build_explainer(candidate.pipeline, x)
    terms = text_term_importance(candidate.pipeline, explainer, x, ["Review"])
    assert terms["Review"]["positive"]


def test_linear_explainer_keeps_only_the_background_mean(synthetic_text_data: tuple) -> None:
    """A linear model's explainer stores a mean vector, not the background rows —
    explainer.joblib stays small however wide the vocabulary is.
    """
    import io

    import joblib

    x, y = synthetic_text_data
    pipeline = build_pipeline(["pay"], ["family"], LogisticRegression(max_iter=1000), ["Review"])
    pipeline.fit(x, y)
    explainer = build_explainer(pipeline, x)
    width = len(transformed_feature_names(pipeline))
    assert np.asarray(explainer.mean).shape == (width,)
    buffer = io.BytesIO()
    joblib.dump(explainer, buffer, compress=True)
    assert buffer.tell() < 200_000


def test_global_shap_importance_works_for_a_linear_model(synthetic_data: tuple) -> None:
    """Regression: LinearExplainer.shap_values() takes no check_additivity argument —
    passing it crashed training whenever a linear candidate won selection.
    """
    x_train, _x_test, y_train, _y_test = synthetic_data
    pipeline = build_pipeline(["numeric_feature"], ["category_feature"], LogisticRegression())
    pipeline.fit(x_train, y_train)
    explainer = build_explainer(pipeline, x_train)

    result = global_shap_importance(pipeline, explainer, x_train, ["numeric_feature", "category_feature"])

    assert result[0]["feature"] == "numeric_feature"
