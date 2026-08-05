"""
- Title:    Model training, Green Code candidate selection, and SHAP explainability
- Author:   ai-circus-framework contributors

Generic across tabular_ml scenarios: numeric vs. categorical features are split by
dtype (etl-tabular already casts non-numeric feature columns to `category`), not by
any scenario-specific hardcoded column list.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import shap
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from training.core.logger import get_logger

logger = get_logger(__name__)

CANDIDATE_ESTIMATORS = {
    "logistic_regression": lambda: LogisticRegression(max_iter=1000),
    "random_forest": lambda: RandomForestClassifier(n_estimators=200, max_depth=8, n_jobs=-1, random_state=0),
}


@dataclass(frozen=True)
class TrainedCandidate:
    """One trained candidate model and its held-out test accuracy."""

    name: str
    pipeline: Pipeline
    test_accuracy: float


def split_features(df: pd.DataFrame, feature_columns: list[str]) -> tuple[list[str], list[str]]:
    """Split feature columns into (numeric, categorical) by dtype."""
    numeric = [c for c in feature_columns if pd.api.types.is_numeric_dtype(df[c])]
    categorical = [c for c in feature_columns if c not in numeric]
    return numeric, categorical


def build_pipeline(numeric_features: list[str], categorical_features: list[str], estimator: object) -> Pipeline:
    """Build a ColumnTransformer (impute+encode) + estimator scikit-learn Pipeline."""
    # Numeric features are scaled: unscaled raw magnitudes (e.g. account balances in
    # the hundreds of thousands) otherwise slow/prevent logistic regression convergence.
    numeric_transformer = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    preprocessor = ColumnTransformer(
        transformers=[
            ("num", numeric_transformer, numeric_features),
            (
                "cat",
                Pipeline([
                    ("impute", SimpleImputer(strategy="most_frequent")),
                    ("encode", OneHotEncoder(handle_unknown="ignore")),
                ]),
                categorical_features,
            ),
        ]
    )
    return Pipeline([("preprocessor", preprocessor), ("model", estimator)])


def train_candidate(
    name: str,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_test: pd.DataFrame,
    y_test: pd.Series,
    numeric_features: list[str],
    categorical_features: list[str],
) -> TrainedCandidate:
    """Train one named candidate estimator and score it on the held-out test set."""
    pipeline = build_pipeline(numeric_features, categorical_features, CANDIDATE_ESTIMATORS[name]())
    pipeline.fit(x_train, y_train)
    accuracy = pipeline.score(x_test, y_test)
    logger.info("Trained candidate {!r}: test accuracy={:.4f}", name, accuracy)
    return TrainedCandidate(name, pipeline, accuracy)


def select_best_candidate(candidates: list[TrainedCandidate], accuracy_gain_threshold: float) -> TrainedCandidate:
    """Green Code policy: candidates are ordered simplest-first in scenario.yaml.

    A more complex candidate only replaces the current best if it beats it by more
    than `accuracy_gain_threshold` — a minor accuracy gain is not worth the added
    training/inference/explainability cost of a more complex model.
    """
    best = candidates[0]
    for candidate in candidates[1:]:
        gain = candidate.test_accuracy - best.test_accuracy
        if gain > accuracy_gain_threshold:
            logger.info(
                "Selecting {!r} over {!r}: accuracy gain {:.4f} > threshold {:.4f}",
                candidate.name,
                best.name,
                gain,
                accuracy_gain_threshold,
            )
            best = candidate
        else:
            logger.info(
                "Keeping simpler {!r} over {!r}: accuracy gain {:.4f} <= threshold {:.4f} (Green Code)",
                best.name,
                candidate.name,
                gain,
                accuracy_gain_threshold,
            )
    return best


def build_explainer(pipeline: Pipeline, x_background: pd.DataFrame) -> shap.Explainer:
    """Build a SHAP explainer appropriate for the pipeline's fitted estimator."""
    model = pipeline.named_steps["model"]
    x_transformed = pipeline.named_steps["preprocessor"].transform(x_background)

    if isinstance(model, RandomForestClassifier):
        return shap.TreeExplainer(
            model, data=x_transformed, feature_perturbation="interventional", model_output="probability"
        )
    return shap.LinearExplainer(model, x_transformed)


def transformed_feature_names(pipeline: Pipeline) -> list[str]:
    """Return the output feature names of the pipeline's preprocessor step."""
    return list(pipeline.named_steps["preprocessor"].get_feature_names_out())
