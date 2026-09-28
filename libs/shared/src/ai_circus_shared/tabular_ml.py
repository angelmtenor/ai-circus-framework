"""Shared SeaweedFS object-key conventions for tabular_ml scenarios.

A single source of truth for the keys etl-tabular writes to and training/prediction
read from, so the three services can't drift out of sync on where artifacts live.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

NORMALIZED_DATASET_KEY = "processed/normalized.parquet"
# Single shared ceiling on how many rows of any one tenant's dataset ever get
# processed — etl-tabular downsamples to this at the source (see
# etl_tabular.core.etl.clean()), so training/evaluation/SHAP can never see more than
# this regardless of how big the raw uploaded/seed CSV is.
MAX_DATASET_ROWS = 30000
MODEL_PIPELINE_KEY = "model/pipeline.joblib"
MODEL_EXPLAINER_KEY = "model/explainer.joblib"
MODEL_METADATA_KEY = "model/metadata.json"
# Regression-only: LightGBM quantile-objective pipelines giving a 90% prediction
# interval around MODEL_PIPELINE_KEY's point estimate — absent for classification
# scenarios (see training/core/training.py's fit_quantile_pipelines()).
MODEL_PIPELINE_LOWER_KEY = "model/pipeline_lower.joblib"
MODEL_PIPELINE_UPPER_KEY = "model/pipeline_upper.joblib"
# Free-text scenarios with a `model.text_challenger`: the sentence-embedding model
# trained next to the deployed one (see scenario_schema.TextChallenger).
MODEL_CHALLENGER_PIPELINE_KEY = "model/challenger_pipeline.joblib"
MODEL_CHALLENGER_EXPLAINER_KEY = "model/challenger_explainer.joblib"

# Key under which MODEL_METADATA_KEY's JSON stores each artifact's checksum (see
# artifact_checksum below) — training writes it, prediction's model_cache verifies it
# before joblib.load()'ing anything, so a partially-overwritten or corrupted artifact
# is rejected loudly instead of silently deserialized.
MODEL_CHECKSUMS_METADATA_FIELD = "checksums"


def artifact_checksum(data: bytes) -> str:
    """Return a SHA-256 hex digest identifying one serialized model artifact's bytes."""
    return hashlib.sha256(data).hexdigest()


# --- Free-text (`type: text`) features -------------------------------------------------
# training vectorizes each text column with its own TfidfVectorizer, registered in the
# ColumnTransformer under `text_transformer_name(column)` — so every vocabulary term's
# transformed name is `text_<column>__<term>`. prediction and training both roll those
# terms back up to `<column>` via `original_feature()`; the prefix (not the term) is what
# identifies the source column, since a TF-IDF term carries no trace of it.
TEXT_TRANSFORMER_PREFIX = "text_"

# sklearn's English stop-word list minus the words that flip or scope a sentence's
# meaning — "no direction", "not transparent" and "never listens" are exactly the
# phrases a review model must be able to read.
KEPT_NEGATIONS = frozenset(
    {
        "no",
        "not",
        "nor",
        "never",
        "none",
        "nothing",
        "nobody",
        "cannot",
        "without",
        "few",
        "too",
        "very",
        "only",
        "every",
    }
)

# Keyword arguments for every text feature's TfidfVectorizer (stop_words is filled in by
# training from sklearn's list minus KEPT_NEGATIONS). Unigrams + bigrams ("upper
# management"), terms in at least 5 documents, capped vocabulary, log-scaled counts.
TEXT_VECTORIZER_PARAMS: dict[str, object] = {
    "ngram_range": (1, 2),
    "min_df": 5,
    "max_features": 3000,
    "sublinear_tf": True,
    "lowercase": True,
    "strip_accents": "unicode",
}


def text_transformer_name(column: str) -> str:
    """ColumnTransformer step name for a text feature column."""
    return f"{TEXT_TRANSFORMER_PREFIX}{column}"


def text_term(transformed_name: str, text_columns: Sequence[str]) -> tuple[str, str] | None:
    """`("Review", "upper management")` for `text_Review__upper management`, else None."""
    step, _, term = transformed_name.partition("__")
    for column in text_columns:
        if step == text_transformer_name(column):
            return column, term
    return None


def original_feature(transformed_name: str, feature_columns: Sequence[str], text_columns: Sequence[str] = ()) -> str:
    """Map one transformed (post-ColumnTransformer) column name back to the feature it
    came from: `text_Review__toxic` -> `Review`, `cat__Geography_France` -> `Geography`,
    `num__Age` -> `Age`, `num__Review__emb_7` (a challenger's embedding dimension) ->
    `Review`. Unknown names are returned unprefixed.
    """
    matched = text_term(transformed_name, text_columns)
    if matched is not None:
        return matched[0]
    unprefixed = transformed_name.split("__", 1)[-1]
    # Longest match wins: `ReviewYear` must not be claimed by a `Review` feature.
    return max((f for f in feature_columns if unprefixed.startswith(f)), key=len, default=unprefixed)


# --- Sentence-embedding challenger (`model.text_challenger`) ---------------------------
# The challenger's pipeline never sees raw text: each text column is replaced, *before*
# the pipeline, by its embedding's dimensions as plain numeric columns
# `<column>__emb_<i>` — so nothing custom is pickled and original_feature() rolls them
# back up to `<column>` with the ordinary prefix rule. Embeddings are computed once per
# dataset on the host GPU (dl-training's `dl-training-embed-texts`) into an .npz cache:
# `hashes` (text_hash of each text), `vectors` (float32, one row per hash), `hf_model_id`.


def embedding_columns(column: str, dimension: int) -> list[str]:
    """Numeric column names standing in for a text column's embedding."""
    return [f"{column}__emb_{i}" for i in range(dimension)]


def text_embedding_cache_key(embedding_model: str) -> str:
    """SeaweedFS key (per tenant, per scenario bucket) of the embedding cache."""
    return f"model/text_embeddings/{embedding_model}.npz"


def text_hash(text: str) -> str:
    """Cache key of one text (its exact characters)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
