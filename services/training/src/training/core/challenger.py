"""
- Title:    Sentence-embedding challenger for free-text scenarios (model.text_challenger)
- Author:   Angel Martinez-Tenor

The deployed model reads text with TF-IDF inside its pipeline. The challenger reads the
same text as a sentence-transformer embedding (llm-gateway's `local-embed`) and is
otherwise identical: same tabular features, same split, same CV, same estimator family.
It is trained, scored and saved next to the champion every run so prediction can serve
both — but never replaces it (see ai_circus_shared.scenario_schema.TextChallenger).

Embeddings are computed *before* the pipeline — each text column becomes plain numeric
columns `<column>__emb_<i>` — so no custom transformer is pickled and prediction can
rebuild the same frame from the gateway at request time. For a whole dataset the vectors
come from the host-GPU cache (`make k3s-text-embeddings`); the gateway, on CPU in the
cluster (~0.6 s per text), only fills small gaps and verifies the cache matches it.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import shap
from ai_circus_shared.embeddings import GatewayEmbeddingProvider
from ai_circus_shared.scenario_schema import TextChallenger
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import embedding_columns, text_embedding_cache_key, text_hash
from sklearn.pipeline import Pipeline

from training.core.logger import get_logger
from training.core.training import (
    build_explainer,
    global_shap_importance,
    holdout_evaluation,
    train_candidate,
    transformed_feature_names,
)

logger = get_logger(__name__)

# More texts missing from the host-GPU cache than this and the challenger is skipped
# (~0.6 s each on the cluster's CPU gateway) with a pointer to `make k3s-text-embeddings`.
MAX_LIVE_EMBEDDINGS = 200
GATEWAY_BATCH = 32
# The cache must reproduce what the gateway serves at predict time.
VERIFY_SAMPLES = 3
MIN_COSINE = 0.99


class ChallengerUnavailableError(RuntimeError):
    """The challenger can't be trained this run (no gateway, no cache) — logged, not fatal."""


@dataclass(frozen=True)
class ChallengerResult:
    """The fitted challenger (refit on every row), its explainer and model-card entry."""

    pipeline: Pipeline
    explainer: shap.Explainer
    metadata: dict[str, Any]


def load_embedding_cache(store: ObjectStore, org_id: str, challenger: TextChallenger) -> dict[str, np.ndarray]:
    """{text_hash: vector} from the host-GPU cache ({} when absent or built with another model)."""
    key = text_embedding_cache_key(challenger.embedding_model)
    if not store.exists(org_id, key):
        return {}
    with np.load(io.BytesIO(store.get(org_id, key)), allow_pickle=False) as data:
        if str(data["hf_model_id"]) != challenger.hf_model_id:
            logger.warning("Embedding cache was built with {}, not {} — ignoring it", data["hf_model_id"], challenger.hf_model_id)
            return {}
        return dict(zip((str(h) for h in data["hashes"]), data["vectors"], strict=True))


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def embed_texts(
    texts: Sequence[str], cache: dict[str, np.ndarray], provider: GatewayEmbeddingProvider | None
) -> np.ndarray:
    """(len(texts), dim) float32 vectors — cached ones reused, the rest from the gateway
    (at most MAX_LIVE_EMBEDDINGS); a few cached ones are re-embedded live to prove the
    cache matches what prediction will get from the gateway."""
    missing = sorted({t for t in texts if text_hash(t) not in cache})
    if len(missing) > MAX_LIVE_EMBEDDINGS or (missing and provider is None):
        raise ChallengerUnavailableError(
            f"{len(missing)} texts are not in the embedding cache — run `make k3s-text-embeddings` "
            "(host GPU) first; the cluster's CPU embedding model would take too long."
        )
    if provider is not None:
        cached = [t for t in texts if text_hash(t) in cache][:VERIFY_SAMPLES]
        if cached:
            live = provider.encode_documents(cached)
            worst = min(_cosine(np.asarray(v), cache[text_hash(t)]) for t, v in zip(cached, live, strict=True))
            if worst < MIN_COSINE:
                raise ChallengerUnavailableError(
                    f"Embedding cache disagrees with llm-gateway (cosine {worst:.3f}) — rebuild it with "
                    "`make k3s-text-embeddings` using the model the gateway serves."
                )
        for start in range(0, len(missing), GATEWAY_BATCH):
            batch = missing[start : start + GATEWAY_BATCH]
            cache.update(zip((text_hash(t) for t in batch), np.asarray(provider.encode_documents(batch)), strict=True))
    return np.stack([cache[text_hash(t)] for t in texts]).astype(np.float32)


def with_embeddings(x: pd.DataFrame, column: str, vectors: np.ndarray) -> pd.DataFrame:
    """`x` with text `column` replaced by its embedding's numeric columns."""
    embedded = pd.DataFrame(vectors, index=x.index, columns=embedding_columns(column, vectors.shape[1]))
    return pd.concat([x.drop(columns=[column]), embedded], axis=1)


def train_text_challenger(
    challenger: TextChallenger,
    *,
    store: ObjectStore,
    org_id: str,
    provider: GatewayEmbeddingProvider | None,
    x: pd.DataFrame,
    y: pd.Series,
    train_index: pd.Index,
    test_index: pd.Index,
    numeric_features: list[str],
    categorical_features: list[str],
    text_features: list[str],
    feature_columns: list[str],
    task_type: str,
    selection_metric: str,
    cv_folds: int,
) -> ChallengerResult:
    """Embed, then train/score `challenger.estimator` on exactly the champion's split."""
    if len(text_features) != 1:
        raise ChallengerUnavailableError("The text challenger supports exactly one text feature.")
    column = text_features[0]
    cache = load_embedding_cache(store, org_id, challenger)
    vectors = embed_texts([str(t) for t in x[column]], cache, provider)
    x_embedded = with_embeddings(x, column, vectors)
    embedded_columns = embedding_columns(column, vectors.shape[1])
    numeric = [*numeric_features, *embedded_columns]

    candidate = train_candidate(
        challenger.estimator,
        x_embedded.loc[train_index],
        y.loc[train_index],
        x_embedded.loc[test_index],
        y.loc[test_index],
        numeric,
        categorical_features,
        task_type,
        selection_metric=selection_metric,
        cv_folds=cv_folds,
    )
    evaluation = (
        holdout_evaluation(candidate.pipeline, x_embedded.loc[test_index], y.loc[test_index])
        if task_type == "classification"
        else None
    )
    candidate.pipeline.fit(x_embedded, y)
    explainer = build_explainer(candidate.pipeline, x_embedded)
    # text_features: each embedding's 1,024 dimensions are summed into one `Review` push per row.
    importance = global_shap_importance(
        candidate.pipeline, explainer, x_embedded, feature_columns, text_features=[column]
    )
    metadata = {
        "name": challenger.estimator,
        "label": challenger.label,
        "embedding_model": challenger.embedding_model,
        "hf_model_id": challenger.hf_model_id,
        "embedding_dim": int(vectors.shape[1]),
        "text_column": column,
        "input_columns": list(x_embedded.columns),
        "transformed_feature_names": transformed_feature_names(candidate.pipeline),
        "selection_score": candidate.selection_score,
        "test_score": candidate.test_score,
        "metrics": candidate.metrics,
        "holdout_evaluation": evaluation,
        "global_feature_importance": importance,
    }
    logger.success(
        "Text challenger {} + {}: selection score {:.4f}", challenger.label, challenger.estimator, candidate.selection_score
    )
    return ChallengerResult(candidate.pipeline, explainer, metadata)
