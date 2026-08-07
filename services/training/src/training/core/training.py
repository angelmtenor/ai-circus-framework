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
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from training.core.logger import get_logger

logger = get_logger(__name__)

# Keyed by task_type, then candidate name (as referenced by scenario.yaml's
# model.candidates) — .score() is accuracy for the classification estimators and R²
# for the regression ones; both are "higher is better", so select_best_candidate()'s
# gain-threshold comparison works unchanged across task types.
CANDIDATE_ESTIMATORS = {
    "classification": {
        "logistic_regression": lambda: LogisticRegression(max_iter=1000),
        "lightgbm": lambda: LGBMClassifier(n_estimators=200, max_depth=6, random_state=0, verbosity=-1),
    },
    "regression": {
        "linear_regression": lambda: LinearRegression(),
        "lightgbm": lambda: LGBMRegressor(n_estimators=200, max_depth=6, random_state=0, verbosity=-1),
    },
}

# 90% prediction interval (5th/95th percentile) — LightGBM's native quantile
# objective is the same technique used for ExtendedRegressor in the smart-data-science
# reference implementation (ml_intervals.py), adapted to this repo's pipeline shape.
INTERVAL_LOWER_ALPHA = 0.05
INTERVAL_UPPER_ALPHA = 0.95


@dataclass(frozen=True)
class TrainedCandidate:
    """One trained candidate model and its held-out test score (accuracy or R²)."""

    name: str
    pipeline: Pipeline
    test_score: float


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
    task_type: str = "classification",
) -> TrainedCandidate:
    """Train one named candidate estimator and score it on the held-out test set."""
    pipeline = build_pipeline(numeric_features, categorical_features, CANDIDATE_ESTIMATORS[task_type][name]())
    pipeline.fit(x_train, y_train)
    score = pipeline.score(x_test, y_test)
    logger.info("Trained candidate {!r}: test score={:.4f}", name, score)
    return TrainedCandidate(name, pipeline, score)


def select_best_candidate(candidates: list[TrainedCandidate], accuracy_gain_threshold: float) -> TrainedCandidate:
    """Green Code policy: candidates are ordered simplest-first in scenario.yaml.

    A more complex candidate only replaces the current best if it beats it by more
    than `accuracy_gain_threshold` — a minor score gain (accuracy for classification,
    R² for regression; both higher-is-better) is not worth the added
    training/inference/explainability cost of a more complex model.
    """
    best = candidates[0]
    for candidate in candidates[1:]:
        gain = candidate.test_score - best.test_score
        if gain > accuracy_gain_threshold:
            logger.info(
                "Selecting {!r} over {!r}: score gain {:.4f} > threshold {:.4f}",
                candidate.name,
                best.name,
                gain,
                accuracy_gain_threshold,
            )
            best = candidate
        else:
            logger.info(
                "Keeping simpler {!r} over {!r}: score gain {:.4f} <= threshold {:.4f} (Green Code)",
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

    if isinstance(model, LGBMClassifier):
        return shap.TreeExplainer(
            model, data=x_transformed, feature_perturbation="interventional", model_output="probability"
        )
    if isinstance(model, LGBMRegressor):
        return shap.TreeExplainer(model, data=x_transformed, feature_perturbation="interventional")
    return shap.LinearExplainer(model, x_transformed)


def fit_quantile_pipelines(
    numeric_features: list[str],
    categorical_features: list[str],
    x: pd.DataFrame,
    y: pd.Series,
) -> tuple[Pipeline, Pipeline]:
    """Fit a (lower, upper) pair of LightGBM quantile-objective pipelines for a 90%
    prediction interval, independent of which regression candidate `select_best_candidate`
    picked — intervals are additive infrastructure, not part of point-accuracy selection.
    """
    lower = build_pipeline(
        numeric_features,
        categorical_features,
        LGBMRegressor(objective="quantile", alpha=INTERVAL_LOWER_ALPHA, n_estimators=200, max_depth=6, verbosity=-1),
    )
    upper = build_pipeline(
        numeric_features,
        categorical_features,
        LGBMRegressor(objective="quantile", alpha=INTERVAL_UPPER_ALPHA, n_estimators=200, max_depth=6, verbosity=-1),
    )
    lower.fit(x, y)
    upper.fit(x, y)
    return lower, upper


def transformed_feature_names(pipeline: Pipeline) -> list[str]:
    """Return the output feature names of the pipeline's preprocessor step."""
    return list(pipeline.named_steps["preprocessor"].get_feature_names_out())
