"""
- Title:    Prediction + SHAP contribution computation
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np
import pandas as pd
import shap
from ai_circus_shared.tabular_ml import embedding_columns, original_feature, text_term, text_transformer_name

from prediction.core.model_cache import ModelArtifacts

# Rows per SHAP batch — a TF-IDF feature's SHAP matrix is dense (an absent word still
# contributes -coef*mean), so a 4,000-review bulk request is explained in slices
# instead of densified in one ~100 MB go. Mirrors training's SHAP_CHUNK_ROWS.
SHAP_CHUNK_ROWS = 500
# Per-word explanations (`explain_text`) are for a person reading one review, not for
# bulk scoring — capped so a request can't ask for 30,000 tokenized explanations. The
# text challenger has the same cap: each record costs an embedding call to llm-gateway.
MAX_TEXT_EXPLAIN_RECORDS = 50


class ChallengerUnavailableError(RuntimeError):
    """No text challenger for this scenario/tenant (not configured, or not trained yet)."""


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
    # Only with explain_text: per text feature, every word of the record's text with
    # its share of that feature's SHAP contribution (see explain_tokens()).
    text_explanations: dict[str, list[dict[str, Any]]] | None = field(default=None)


def _shap_values(explainer: shap.Explainer, x: np.ndarray) -> Any:
    """Raw SHAP values for a dense block. check_additivity=False only exists on
    TreeExplainer: SHAP's self-check asserts sum(contributions) + base value == the
    model's output within a tight tolerance, and LightGBM under interventional
    perturbation with a subsampled background legitimately misses it by a few percent
    on some inputs (seen for real on cnc_surface_finish's trained model: 3.656 vs
    3.539, i.e. one bad record 500ing a whole batch). The explanation is still the same
    additive decomposition — only the assert is skipped, matching SHAP's own guidance
    for this known false positive. LinearExplainer takes no such argument (and is exact).
    """
    if isinstance(explainer, shap.TreeExplainer):
        return explainer.shap_values(x, check_additivity=False)
    # pyrefly: ignore [missing-attribute]
    return explainer.shap_values(x)


def shap_matrix(explainer: shap.Explainer, x_transformed: Any) -> np.ndarray:
    """(n_rows, n_transformed_columns) positive-class SHAP values, SHAP_CHUNK_ROWS rows
    at a time. Each slice is densified: a sparse OneHotEncoder output crashes
    LightGBM's SHAP path once a categorical's cardinality is high enough (confirmed
    empirically at 50 categories) — see training.core.training.build_explainer.
    """
    chunks = []
    for start in range(0, x_transformed.shape[0], SHAP_CHUNK_ROWS):
        chunk = x_transformed[start : start + SHAP_CHUNK_ROWS]
        if hasattr(chunk, "toarray"):
            chunk = chunk.toarray()
        values = np.asarray(_shap_values(explainer, chunk))
        # Binary-classification TreeExplainer with model_output="probability" returns
        # (n_samples, n_features, n_classes); keep just the positive class's
        # contributions. (Regression explainers return a plain 2D array.)
        if values.ndim == 3:
            values = values[:, :, 1]
        chunks.append(values)
    if not chunks:
        return np.zeros((0, x_transformed.shape[1]))
    return np.vstack(chunks)


def text_columns(artifacts: ModelArtifacts) -> list[str]:
    """The model's free-text feature columns (recorded by training; [] for models
    trained before text features existed, or without any).
    """
    return list(artifacts.metadata.get("text_columns") or [])


def prepare_text(x: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Missing text -> "" and everything else -> str: TF-IDF rejects NaN/None documents."""
    if not columns:
        return x
    x = x.copy()
    for column in columns:
        x[column] = x[column].where(x[column].notna(), "").astype(str)
    return x


def explain_tokens(
    artifacts: ModelArtifacts, column: str, text: str, row_shap: np.ndarray, names: list[str]
) -> list[dict[str, Any]]:
    """Split one text feature's per-term SHAP values back onto the words of `text`.

    Tokenizes the *original* string with the fitted vectorizer's own token_pattern (so
    `start`/`end` index into what the user typed) and normalizes each token with its
    preprocessor for the vocabulary lookup. A unigram's value goes to its token (shared
    evenly between repeats), a bigram's is split half/half between its two tokens —
    bigrams are formed across stop words exactly as the vectorizer does. Words outside
    the vocabulary (or stop words) get weight 0. Terms *absent* from the text also carry
    SHAP value (relative to an average document); that remainder is the feature's total
    contribution minus the sum of these weights.
    """
    vectorizer = artifacts.pipeline.named_steps["preprocessor"].named_transformers_[text_transformer_name(column)]
    preprocess = vectorizer.build_preprocessor()
    stop_words = set(vectorizer.get_stop_words() or ())
    column_of_term = {}
    for j, name in enumerate(names):
        matched = text_term(name, [column])
        if matched is not None:
            column_of_term[matched[1]] = j

    tokens = [
        {"text": m.group(), "start": m.start(), "end": m.end(), "norm": preprocess(m.group())}
        for m in re.finditer(vectorizer.token_pattern, text)
    ]
    kept = [t for t in tokens if t["norm"] not in stop_words]
    grams: list[tuple[str, list[dict[str, Any]]]] = [(t["norm"], [t]) for t in kept]
    grams += [(f"{a['norm']} {b['norm']}", [a, b]) for a, b in pairwise(kept)]
    occurrences: dict[str, int] = {}
    for term, _ in grams:
        occurrences[term] = occurrences.get(term, 0) + 1

    weights = [0.0] * len(tokens)
    index_of = {id(t): i for i, t in enumerate(tokens)}
    for term, members in grams:
        j = column_of_term.get(term)
        if j is None:
            continue
        share = float(row_shap[j]) / occurrences[term] / len(members)
        for member in members:
            weights[index_of[id(member)]] += share
    return [
        {"text": t["text"], "start": t["start"], "end": t["end"], "weight": round(w, 5)}
        for t, w in zip(tokens, weights, strict=True)
    ]


def predict(
    artifacts: ModelArtifacts, records: pd.DataFrame, *, explain: bool = True, explain_text: bool = False
) -> list[PredictionResult]:
    """Return prediction + per-feature SHAP contributions for each record.

    Contributions are keyed by *transformed* feature names (post one-hot-encoding) —
    e.g. `cat__Geography_France`, not `Geography` — since that's the level at which
    the explainer computed them. The one exception is a free-text feature, whose
    thousands of TF-IDF term columns are summed server-side into one entry keyed by the
    column name (e.g. `Review`); `explain_text` additionally returns how that sum
    splits over the words of each record's text. `explain=False` skips SHAP entirely
    (empty contributions) — for scoring a whole dataset at once, where TreeExplainer's
    interventional SHAP (~5 ms/row) would dominate and only the probabilities are drawn.
    """
    feature_columns = artifacts.metadata["feature_columns"]
    missing = [c for c in feature_columns if c not in records.columns]
    if missing:
        raise MissingFeatureColumnsError(missing)
    text_features = text_columns(artifacts)
    x = prepare_text(records.loc[:, feature_columns], text_features)

    if artifacts.metadata["task_type"] == "regression":
        predictions = np.asarray(artifacts.pipeline.predict(x))
    else:
        predictions = np.asarray(artifacts.pipeline.predict_proba(x))[:, 1]
    lower = artifacts.pipeline_lower.predict(x) if artifacts.pipeline_lower is not None else None
    upper = artifacts.pipeline_upper.predict(x) if artifacts.pipeline_upper is not None else None
    if not explain:
        return [
            PredictionResult(
                prediction=round(float(prediction), 4),
                contributions={},
                prediction_lower=round(float(lower[i]), 4) if lower is not None else None,
                prediction_upper=round(float(upper[i]), 4) if upper is not None else None,
            )
            for i, prediction in enumerate(predictions)
        ]

    x_transformed = artifacts.pipeline.named_steps["preprocessor"].transform(x)
    shap_values = shap_matrix(artifacts.explainer, x_transformed)
    feature_names: list[str] = artifacts.metadata["transformed_feature_names"]

    # Column groups: every non-text transformed column is reported as-is; each text
    # feature's term columns collapse into one sum.
    term_columns: dict[str, list[int]] = {column: [] for column in text_features}
    plain_columns: list[int] = []
    for j, name in enumerate(feature_names):
        matched = text_term(name, text_features)
        if matched is None:
            plain_columns.append(j)
        else:
            term_columns[matched[0]].append(j)

    results = []
    for i, prediction in enumerate(predictions):
        row = shap_values[i]
        contributions = {feature_names[j]: round(float(row[j]), 4) for j in plain_columns}
        for column, indices in term_columns.items():
            contributions[column] = round(float(row[indices].sum()), 4)
        explanations = None
        if explain_text and text_features:
            explanations = {
                column: explain_tokens(artifacts, column, str(x.iloc[i][column]), row, feature_names)
                for column in text_features
            }
        results.append(
            PredictionResult(
                prediction=round(float(prediction), 4),
                contributions=contributions,
                prediction_lower=round(float(lower[i]), 4) if lower is not None else None,
                prediction_upper=round(float(upper[i]), 4) if upper is not None else None,
                text_explanations=explanations,
            )
        )
    return results


def predict_challenger(
    artifacts: ModelArtifacts, records: pd.DataFrame, embed: Callable[[list[str]], list[list[float]]]
) -> list[PredictionResult]:
    """Score records with the sentence-embedding challenger: the text column is embedded
    via `embed` (llm-gateway's embedding model, exactly as training did) and replaced by
    its dimensions, then the challenger pipeline predicts and SHAP explains. The
    embedding dimensions' contributions are summed into one entry keyed by the text
    column (no per-word explanation: a dimension is not a word).
    """
    meta = artifacts.metadata.get("challenger")
    if not meta or artifacts.challenger_pipeline is None:
        raise ChallengerUnavailableError(
            "No text challenger trained for this scenario yet — run `make k3s-text-embeddings`."
        )
    feature_columns = artifacts.metadata["feature_columns"]
    missing = [c for c in feature_columns if c not in records.columns]
    if missing:
        raise MissingFeatureColumnsError(missing)
    column = meta["text_column"]
    x = prepare_text(records.loc[:, feature_columns], [column])
    vectors = np.asarray(embed([str(t) for t in x[column]]), dtype=np.float32)
    embedded = pd.DataFrame(vectors, index=x.index, columns=embedding_columns(column, vectors.shape[1]))
    x_embedded = pd.concat([x.drop(columns=[column]), embedded], axis=1).loc[:, meta["input_columns"]]

    if artifacts.metadata["task_type"] == "regression":
        predictions = np.asarray(artifacts.challenger_pipeline.predict(x_embedded))
    else:
        predictions = np.asarray(artifacts.challenger_pipeline.predict_proba(x_embedded))[:, 1]
    transformed = artifacts.challenger_pipeline.named_steps["preprocessor"].transform(x_embedded)
    shap_values = shap_matrix(artifacts.challenger_explainer, transformed)
    names: list[str] = meta["transformed_feature_names"]
    groups = [original_feature(n, feature_columns, [column]) for n in names]
    embedding_indices = [j for j, g in enumerate(groups) if g == column]
    other_indices = [j for j, g in enumerate(groups) if g != column]
    return [
        PredictionResult(
            prediction=round(float(prediction), 4),
            contributions={
                **{names[j]: round(float(shap_values[i, j]), 4) for j in other_indices},
                column: round(float(shap_values[i, embedding_indices].sum()), 4),
            },
        )
        for i, prediction in enumerate(predictions)
    ]
