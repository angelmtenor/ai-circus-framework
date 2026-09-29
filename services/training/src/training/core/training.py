"""
- Title:    Model training, Green Code candidate selection, and SHAP explainability
- Author:   Angel Martinez-Tenor

Generic across tabular_ml scenarios: numeric vs. categorical features are split by
dtype (etl-tabular already casts non-numeric feature columns to `category`), not by
any scenario-specific hardcoded column list. Free-text features (`type: text` in the
scenario's feature_schema) are named explicitly by the caller and get their own TF-IDF
step, so the model reads raw text and SHAP explains it term by term.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import shap
from ai_circus_shared.tabular_ml import KEPT_NEGATIONS, TEXT_VECTORIZER_PARAMS, original_feature, text_transformer_name
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, r2_score, roc_auc_score, roc_curve
from sklearn.model_selection import KFold, StratifiedKFold, cross_validate
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from training.core.logger import get_logger

logger = get_logger(__name__)

# Gradient boosting sized for small tables (hundreds to a few thousand rows): shallow
# trees, a slow learning rate, row/column subsampling and L2 — the plain `lightgbm`
# defaults memorise a table that small. Measured on `titanic` (5-fold CV ROC AUC on
# the training split): 0.885 for this vs 0.860 for `lightgbm` and 0.872 for logistic
# regression.
_SMALL_DATA_LGBM: dict[str, Any] = {
    "n_estimators": 300,
    "learning_rate": 0.03,
    "num_leaves": 8,
    "max_depth": 4,
    "min_child_samples": 10,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.7,
    "reg_lambda": 2.0,
    "random_state": 0,
    "verbosity": -1,
}

# Keyed by task_type, then candidate name (as referenced by scenario.yaml's
# model.candidates) — .score() is accuracy for the classification estimators and R²
# for the regression ones; every selection metric (accuracy, ROC AUC, R²) is "higher
# is better", so select_best_candidate()'s gain-threshold comparison works unchanged.
CANDIDATE_ESTIMATORS = {
    "classification": {
        "logistic_regression": lambda: LogisticRegression(max_iter=1000),
        "lightgbm": lambda: LGBMClassifier(n_estimators=200, max_depth=6, random_state=0, verbosity=-1),
        "lightgbm_small_data": lambda: LGBMClassifier(**_SMALL_DATA_LGBM),
    },
    "regression": {
        "linear_regression": lambda: LinearRegression(),
        "lightgbm": lambda: LGBMRegressor(n_estimators=200, max_depth=6, random_state=0, verbosity=-1),
        "lightgbm_small_data": lambda: LGBMRegressor(**_SMALL_DATA_LGBM),
    },
}

# scenario.yaml's model.selection_metric when unset — the historical behaviour.
DEFAULT_SELECTION_METRIC = {"classification": "accuracy", "regression": "r2"}
# Every metric reported per candidate (hold-out, and CV when enabled) — the selection
# metric is always one of these.
REPORTED_METRICS = {"classification": ("accuracy", "roc_auc"), "regression": ("r2",)}
# Points kept from the hold-out ROC curve in metadata.json — plenty for a smooth plot.
ROC_CURVE_MAX_POINTS = 80

# 90% prediction interval (5th/95th percentile) — LightGBM's native quantile
# objective is the same technique used for ExtendedRegressor in the smart-data-science
# reference implementation (ml_intervals.py), adapted to this repo's pipeline shape.
INTERVAL_LOWER_ALPHA = 0.05
INTERVAL_UPPER_ALPHA = 0.95

# sklearn's English stop words, minus the negations a review model must read.
TEXT_STOP_WORDS = sorted(ENGLISH_STOP_WORDS - KEPT_NEGATIONS)
# Rows per SHAP batch: a TF-IDF feature's SHAP matrix is dense (an absent word still
# contributes -coef*mean), so a few thousand rows x a 3,000-term vocabulary are
# explained in slices rather than densified in one go.
SHAP_CHUNK_ROWS = 500
# text_term_importance(): terms per direction, and the minimum number of documents a
# term must appear in to be ranked (rarer terms are noise at this data size).
TOP_TERMS = 25
TOP_TERMS_MIN_DOCS = 10


@dataclass(frozen=True)
class TrainedCandidate:
    """One trained candidate model (fit on the training split) and its scores.

    `test_score` keeps its historical meaning (hold-out accuracy / R² — what the
    assistant quotes); `selection_score` is what select_best_candidate() compares (the
    scenario's selection metric, cross-validated when `cv_folds` is set); `metrics`
    holds every reported value (`holdout_<metric>`, `cv_<metric>_mean`/`_std`).
    """

    name: str
    pipeline: Pipeline
    test_score: float
    selection_score: float
    metrics: dict[str, float]


def split_features(
    df: pd.DataFrame, feature_columns: list[str], text_features: Sequence[str] = ()
) -> tuple[list[str], list[str]]:
    """Split feature columns into (numeric, categorical) by dtype, leaving out the
    free-text ones (`text_features`, taken from the scenario schema — never inferred
    from dtype, where a review is indistinguishable from a category label).
    """

    def is_numeric(column: str) -> bool:
        return pd.api.types.is_numeric_dtype(df[column]) and not pd.api.types.is_bool_dtype(df[column])

    tabular = [c for c in feature_columns if c not in text_features]
    numeric = [c for c in tabular if is_numeric(c)]
    categorical = [c for c in tabular if c not in numeric]
    return numeric, categorical


def build_pipeline(
    numeric_features: list[str],
    categorical_features: list[str],
    estimator: object,
    text_features: Sequence[str] = (),
) -> Pipeline:
    """Build a ColumnTransformer (impute+encode, TF-IDF per text feature) + estimator
    scikit-learn Pipeline.
    """
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
            # One vectorizer per text column, selected by a bare string (not a list) so
            # it receives the 1-D sequence of documents TF-IDF expects. The step name
            # prefixes every term (`text_Review__upper management`) — how training and
            # prediction trace a term back to its column (tabular_ml.original_feature).
            *(
                (
                    text_transformer_name(column),
                    TfidfVectorizer(stop_words=TEXT_STOP_WORDS, **TEXT_VECTORIZER_PARAMS),
                    column,
                )  # type: ignore[arg-type]
                for column in text_features
            ),
        ]
    )
    return Pipeline([("preprocessor", preprocessor), ("model", estimator)])


def holdout_metrics(pipeline: Pipeline, x_test: pd.DataFrame, y_test: pd.Series, task_type: str) -> dict[str, float]:
    """Every REPORTED_METRICS value of a fitted pipeline on the hold-out, as `holdout_<metric>`."""
    if task_type == "regression":
        return {"holdout_r2": round(float(r2_score(y_test, pipeline.predict(x_test))), 4)}
    metrics = {"holdout_accuracy": round(float(accuracy_score(y_test, pipeline.predict(x_test))), 4)}
    if y_test.nunique() == 2:  # ROC AUC is defined for a binary target with both classes present
        proba = pipeline.predict_proba(x_test)[:, 1]
        metrics["holdout_roc_auc"] = round(float(roc_auc_score(y_test == pipeline.classes_[1], proba)), 4)
    return metrics


def cross_validated_metrics(
    estimator_name: str,
    x: pd.DataFrame,
    y: pd.Series,
    numeric_features: list[str],
    categorical_features: list[str],
    task_type: str,
    folds: int,
    text_features: Sequence[str] = (),
) -> dict[str, float]:
    """k-fold CV (stratified for classification) of a fresh pipeline on `x`/`y` — the
    training split only, so the hold-out stays untouched for the final report.
    """
    pipeline = build_pipeline(
        numeric_features, categorical_features, CANDIDATE_ESTIMATORS[task_type][estimator_name](), text_features
    )
    splitter = (
        StratifiedKFold(n_splits=folds, shuffle=True, random_state=0)
        if task_type == "classification"
        else KFold(n_splits=folds, shuffle=True, random_state=0)
    )
    names = [m for m in REPORTED_METRICS[task_type] if m != "roc_auc" or y.nunique() == 2]
    scores = cross_validate(pipeline, x, y, cv=splitter, scoring=names, n_jobs=1)
    metrics: dict[str, float] = {}
    for name in names:
        values = np.asarray(scores[f"test_{name}"], dtype=float)
        metrics[f"cv_{name}_mean"] = round(float(values.mean()), 4)
        metrics[f"cv_{name}_std"] = round(float(values.std()), 4)
    return metrics


def train_candidate(
    name: str,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_test: pd.DataFrame,
    y_test: pd.Series,
    numeric_features: list[str],
    categorical_features: list[str],
    task_type: str = "classification",
    *,
    selection_metric: str | None = None,
    cv_folds: int = 0,
    text_features: Sequence[str] = (),
) -> TrainedCandidate:
    """Train one named candidate estimator on the training split and score it — on the
    hold-out, plus k-fold CV on the training split when `cv_folds` >= 2.
    """
    metric = selection_metric or DEFAULT_SELECTION_METRIC[task_type]
    metrics: dict[str, float] = {}
    if cv_folds >= 2:
        metrics.update(
            cross_validated_metrics(
                name, x_train, y_train, numeric_features, categorical_features, task_type, cv_folds, text_features
            )
        )
    pipeline = build_pipeline(
        numeric_features, categorical_features, CANDIDATE_ESTIMATORS[task_type][name](), text_features
    )
    pipeline.fit(x_train, y_train)
    score = pipeline.score(x_test, y_test)
    metrics.update(holdout_metrics(pipeline, x_test, y_test, task_type))
    selection_key = f"cv_{metric}_mean" if cv_folds >= 2 else f"holdout_{metric}"
    selection_score = metrics.get(selection_key, score)
    logger.info("Trained candidate {!r}: {}={:.4f} (test score={:.4f})", name, selection_key, selection_score, score)
    return TrainedCandidate(name, pipeline, score, selection_score, metrics)


def holdout_evaluation(pipeline: Pipeline, x_test: pd.DataFrame, y_test: pd.Series) -> dict[str, Any] | None:
    """The selected classifier's ROC curve and confusion matrix (at 0.5) on the
    untouched hold-out — computed *before* the final refit on every row, so unlike
    prediction's live /dataset/{slug}/evaluation (which scores the refit, deployed
    weights on rows they were fit on) these numbers carry no leakage. None for a
    non-binary target.
    """
    if y_test.nunique() != 2:
        return None
    positive = pipeline.classes_[1]
    actual = (y_test == positive).to_numpy()
    proba = pipeline.predict_proba(x_test)[:, 1]
    fpr, tpr, _ = roc_curve(actual, proba)
    if len(fpr) > ROC_CURVE_MAX_POINTS:
        keep = np.unique(np.linspace(0, len(fpr) - 1, ROC_CURVE_MAX_POINTS).astype(int))
        fpr, tpr = fpr[keep], tpr[keep]
    tn, fp, fn, tp = confusion_matrix(actual, proba >= 0.5, labels=[False, True]).ravel()
    return {
        "n": len(y_test),
        "threshold": 0.5,
        "positive_rate": round(float(actual.mean()), 4),
        "roc_curve": {"fpr": [round(float(v), 4) for v in fpr], "tpr": [round(float(v), 4) for v in tpr]},
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def select_best_candidate(candidates: list[TrainedCandidate], accuracy_gain_threshold: float) -> TrainedCandidate:
    """Green Code policy: candidates are ordered simplest-first in scenario.yaml.

    A more complex candidate only replaces the current best if it beats it by more
    than `accuracy_gain_threshold` — a minor gain in the selection score (the
    scenario's selection metric: accuracy / ROC AUC / R², all higher-is-better,
    cross-validated when enabled) is not worth the added training/inference/
    explainability cost of a more complex model.
    """
    best = candidates[0]
    for candidate in candidates[1:]:
        gain = candidate.selection_score - best.selection_score
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
    if not isinstance(model, LGBMClassifier | LGBMRegressor):
        # Linear model: interventional SHAP only needs the background *mean* —
        # (mean, cov=None) keeps explainer.joblib a single vector however wide the
        # TF-IDF vocabulary, and uses every row rather than a 100-row sample.
        mean = np.asarray(x_transformed.mean(axis=0)).ravel()
        return shap.LinearExplainer(model, (mean, None))
    # OneHotEncoder emits a sparse matrix; LightGBM's own predict path handles that
    # fine, but shap.TreeExplainer's internal background-subsampling (see
    # "Background dataset has N samples but max_samples=100" above) hits a real
    # LightGBM C-extension crash ("Found a NULL input array") once a categorical
    # feature's cardinality is high enough that a random 100-row subsample lands on
    # a very sparse slice — confirmed empirically at 50 categories (never surfaced
    # by this repo's other scenarios, whose categoricals top out at a handful).
    # Densifying costs nothing at this repo's MAX_DATASET_ROWS scale (well under
    # 30,000 rows x a few hundred one-hot columns) and changes no explanation
    # values for any already-passing scenario.
    if hasattr(x_transformed, "toarray"):
        x_transformed = x_transformed.toarray()

    if isinstance(model, LGBMClassifier):
        return shap.TreeExplainer(
            model, data=x_transformed, feature_perturbation="interventional", model_output="probability"
        )
    return shap.TreeExplainer(model, data=x_transformed, feature_perturbation="interventional")


def fit_quantile_pipelines(
    numeric_features: list[str],
    categorical_features: list[str],
    x: pd.DataFrame,
    y: pd.Series,
    text_features: Sequence[str] = (),
) -> tuple[Pipeline, Pipeline]:
    """Fit a (lower, upper) pair of LightGBM quantile-objective pipelines for a 90%
    prediction interval, independent of which regression candidate `select_best_candidate`
    picked — intervals are additive infrastructure, not part of point-accuracy selection.
    """
    lower = build_pipeline(
        numeric_features,
        categorical_features,
        LGBMRegressor(objective="quantile", alpha=INTERVAL_LOWER_ALPHA, n_estimators=200, max_depth=6, verbosity=-1),
        text_features,
    )
    upper = build_pipeline(
        numeric_features,
        categorical_features,
        LGBMRegressor(objective="quantile", alpha=INTERVAL_UPPER_ALPHA, n_estimators=200, max_depth=6, verbosity=-1),
        text_features,
    )
    lower.fit(x, y)
    upper.fit(x, y)
    return lower, upper


def transformed_feature_names(pipeline: Pipeline) -> list[str]:
    """Return the output feature names of the pipeline's preprocessor step."""
    return list(pipeline.named_steps["preprocessor"].get_feature_names_out())


def _aggregate_by_feature(
    names: list[str], values: np.ndarray, feature_columns: list[str], text_features: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Aggregate per-transformed-(one-hot / TF-IDF term)-column values back to the
    original feature they came from (e.g. `cat__Geography_France` -> `Geography`,
    `text_Review__politics` -> `Review`), summed and ranked descending. Mirrors
    prediction/core/dataset.py's identically-named helper — kept as a small duplicate
    rather than a new libs/shared dependency, since it's the only numpy-dependent code
    either service would need to share for this (the name mapping itself is shared:
    tabular_ml.original_feature).
    """
    totals: dict[str, float] = {}
    for name, value in zip(names, values, strict=True):
        original = original_feature(name, feature_columns, text_features)
        totals[original] = totals.get(original, 0.0) + float(value)

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [{"feature": name, "importance": round(value, 4)} for name, value in ranked]


def _shap_values(explainer: shap.Explainer, x: np.ndarray) -> Any:
    """Raw SHAP values for a dense block. check_additivity=False only exists on
    TreeExplainer (see prediction.core.predict's matching note: a known SHAP/LightGBM
    false positive that would otherwise fail the job on one row) — LinearExplainer's
    shap_values() takes no such argument and is exact anyway.
    """
    if isinstance(explainer, shap.TreeExplainer):
        return explainer.shap_values(x, check_additivity=False)
    # pyrefly: ignore [missing-attribute]
    return explainer.shap_values(x)


def shap_matrix(explainer: shap.Explainer, x_transformed: Any) -> np.ndarray:
    """(n_rows, n_transformed_columns) positive-class SHAP values, explained
    SHAP_CHUNK_ROWS rows at a time (each slice densified — see build_explainer's
    comment on why LightGBM needs dense input).
    """
    chunks = []
    for start in range(0, x_transformed.shape[0], SHAP_CHUNK_ROWS):
        chunk = x_transformed[start : start + SHAP_CHUNK_ROWS]
        if hasattr(chunk, "toarray"):
            chunk = chunk.toarray()
        values = np.asarray(_shap_values(explainer, chunk))
        if values.ndim == 3:  # binary-classification TreeExplainer: (n, features, classes)
            values = values[:, :, 1]
        chunks.append(values)
    return np.vstack(chunks)


def out_of_fold_scores(
    estimator_name: str,
    x: pd.DataFrame,
    y: pd.Series,
    numeric_features: list[str],
    categorical_features: list[str],
    folds: int,
    text_features: Sequence[str] = (),
) -> dict[str, Any]:
    """Every row's probability and SHAP contributions from a copy of the selected
    model trained on the *other* folds only (stratified k-fold cross-fitting over the
    whole dataset) — what the model says about someone whose label it never saw.

    The deployed model is refit on every row, so on a small dataset its scores of those
    same rows are close to memorised (every positive near 1); these are the honest
    ones to show next to the real outcome. Contributions use prediction's format
    (transformed column -> value; a text feature's terms summed into one entry), so
    ui-react explains them exactly like a live /predict response.
    """
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=0)
    rows: dict[str, dict[str, Any]] = {}
    probabilities = np.zeros(len(x))
    for fold, (train_index, test_index) in enumerate(splitter.split(x, y)):
        estimator = CANDIDATE_ESTIMATORS["classification"][estimator_name]()
        pipeline = build_pipeline(numeric_features, categorical_features, estimator, text_features)
        pipeline.fit(x.iloc[train_index], y.iloc[train_index])
        explainer = build_explainer(pipeline, x.iloc[train_index])
        held_out = x.iloc[test_index]
        proba = pipeline.predict_proba(held_out)[:, 1]
        probabilities[test_index] = proba
        values = shap_matrix(explainer, pipeline.named_steps["preprocessor"].transform(held_out))
        names = transformed_feature_names(pipeline)
        groups: dict[str, list[int]] = {}
        for j, name in enumerate(names):
            column = original_feature(name, list(x.columns), text_features)
            groups.setdefault(column if column in text_features else name, []).append(j)
        for i, row_id in enumerate(held_out.index):
            rows[str(row_id)] = {
                "probability": round(float(proba[i]), 4),
                "fold": fold,
                "contributions": {key: round(float(values[i, idx].sum()), 4) for key, idx in groups.items()},
            }
    positive = y == sorted(y.unique())[-1]
    return {
        "model_name": estimator_name,
        "folds": folds,
        "roc_auc": round(float(roc_auc_score(positive, probabilities)), 4) if positive.nunique() == 2 else None,
        "rows": rows,
    }


def global_shap_importance(
    pipeline: Pipeline,
    explainer: shap.Explainer,
    x: pd.DataFrame,
    feature_columns: list[str],
    sample_size: int = 500,
    text_features: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Global feature importance (mean(|SHAP value|) per feature) computed once at
    training time, over a sample of the full dataset the final pipeline was refit on.

    Written into MODEL_METADATA_KEY so `assistant`'s grounding prompt can cite real
    per-feature magnitudes without a live round-trip to prediction's own (identically
    computed, but on-demand) `/dataset/{slug}/explainability` endpoint — that endpoint
    stays as the dashboard's live/re-computable path; this is a cached snapshot for chat.
    """
    if len(x) > sample_size:
        idx = np.linspace(0, len(x) - 1, sample_size, dtype=int)
        x = x.iloc[idx]

    x_transformed = pipeline.named_steps["preprocessor"].transform(x)
    shap_values = shap_matrix(explainer, x_transformed)
    if text_features:
        # A text feature's importance is the size of its *total* push per row (the
        # sum over its terms), not the sum of every term's separate |push| — the
        # latter would rank a 3,000-term vocabulary above any single column.
        names = transformed_feature_names(pipeline)
        per_feature = (
            pd
            .DataFrame(shap_values, columns=names)
            .T.groupby([original_feature(n, feature_columns, text_features) for n in names])
            .sum()
            .T
        )
        mean_abs_by_feature = per_feature.abs().mean(axis=0)
        return _aggregate_by_feature(
            list(mean_abs_by_feature.index), mean_abs_by_feature.to_numpy(), feature_columns, ()
        )

    mean_abs = np.abs(shap_values).mean(axis=0)
    return _aggregate_by_feature(transformed_feature_names(pipeline), mean_abs, feature_columns)


def text_term_importance(
    pipeline: Pipeline,
    explainer: shap.Explainer,
    x: pd.DataFrame,
    text_features: Sequence[str],
    top_k: int = TOP_TERMS,
    min_docs: int = TOP_TERMS_MIN_DOCS,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Per text feature, the terms that push predictions up (`positive`) and down
    (`negative`) the most: each term's mean SHAP value over the documents that contain
    it (terms in fewer than `min_docs` documents are skipped as noise). Computed from
    SHAP rather than model coefficients, so it means the same thing for a linear model
    and for gradient boosting. Written into the model card (the Tutorial's `top_terms`
    widget and the assistant's grounding prompt).
    """
    if not text_features:
        return {}
    x_transformed = pipeline.named_steps["preprocessor"].transform(x)
    shap_values = shap_matrix(explainer, x_transformed)
    present = x_transformed.tocsc() if hasattr(x_transformed, "tocsc") else x_transformed
    names = transformed_feature_names(pipeline)
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for column in text_features:
        prefix = f"{text_transformer_name(column)}__"
        terms: list[dict[str, Any]] = []
        for j, name in enumerate(names):
            if not name.startswith(prefix):
                continue
            rows = present[:, j].nonzero()[0]
            if len(rows) < min_docs:
                continue
            terms.append({
                "term": name[len(prefix) :],
                "weight": round(float(shap_values[rows, j].mean()), 4),
                "docs": len(rows),
            })
        ranked = sorted(terms, key=lambda t: t["weight"])
        result[column] = {
            "positive": [t for t in reversed(ranked) if t["weight"] > 0][:top_k],
            "negative": [t for t in ranked if t["weight"] < 0][:top_k],
        }
    return result
