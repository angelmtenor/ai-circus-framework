"""Tests for the sentence-embedding text challenger (model.text_challenger)."""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest
from ai_circus_shared.scenario_schema import TextChallenger
from ai_circus_shared.tabular_ml import text_embedding_cache_key, text_hash

from training.core.challenger import (
    MAX_LIVE_EMBEDDINGS,
    ChallengerUnavailableError,
    embed_texts,
    load_embedding_cache,
    train_text_challenger,
    with_embeddings,
)

CHALLENGER = TextChallenger(hf_model_id="org/model", label="test embeddings")


def _vector(text: str) -> np.ndarray:
    """A deterministic 4-d 'embedding' that separates toxic from healthy words."""
    toxic = sum(w in text for w in ("politics", "blame", "lies", "micromanagement"))
    healthy = sum(w in text for w in ("trust", "mentoring", "supportive", "listens"))
    return np.array([toxic, healthy, len(text) / 100, 1.0], dtype=np.float32)


TOXIC = ["politics and blame", "lies, micromanagement"]
GOOD = ["trust", "mentoring"]


class FakeProvider:
    """Stand-in for GatewayEmbeddingProvider; `drift` perturbs what it returns."""

    def __init__(self, drift: float = 0.0) -> None:
        """No calls yet; `drift` shifts every vector it returns."""
        self.calls: list[list[str]] = []
        self.drift = drift

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        """Record the batch and return its (drifted) toy vectors."""
        self.calls.append(list(texts))
        return [(_vector(t) + self.drift * np.array([1.0, -1.0, 0.0, -1.0])).tolist() for t in texts]


class FakeStore:
    """In-memory ObjectStore."""

    def __init__(self) -> None:
        """Empty store."""
        self.objects: dict[tuple[str, str], bytes] = {}

    def exists(self, org_id: str, path: str) -> bool:
        """Whether an object exists."""
        return (org_id, path) in self.objects

    def get(self, org_id: str, path: str) -> bytes:
        """Read an object (KeyError when absent)."""
        return self.objects[org_id, path]

    def put(self, org_id: str, path: str, data: bytes) -> str:
        """Store an object."""
        self.objects[org_id, path] = data
        return path


def _store_with_cache(texts: list[str], hf_model_id: str = "org/model") -> FakeStore:
    store = FakeStore()
    buffer = io.BytesIO()
    np.savez_compressed(
        buffer,
        hashes=np.asarray([text_hash(t) for t in texts]),
        vectors=np.stack([_vector(t) for t in texts]),
        hf_model_id=np.asarray(hf_model_id),
    )
    store.put("demo", text_embedding_cache_key("local-embed"), buffer.getvalue())
    return store


def test_cached_vectors_are_reused_and_only_gaps_go_to_the_gateway() -> None:
    store = _store_with_cache(["toxic politics", "great mentoring"])
    cache = load_embedding_cache(store, "demo", CHALLENGER)  # type: ignore[arg-type]
    provider = FakeProvider()

    vectors = embed_texts(["toxic politics", "great mentoring", "blame culture"], cache, provider)  # type: ignore[arg-type]

    assert vectors.shape == (3, 4) and vectors.dtype == np.float32
    assert provider.calls[-1] == ["blame culture"]  # the one gap (after a verification call)
    assert vectors[2].tolist() == _vector("blame culture").tolist()


def test_a_cache_that_disagrees_with_the_gateway_is_rejected() -> None:
    store = _store_with_cache(["toxic politics"])
    cache = load_embedding_cache(store, "demo", CHALLENGER)  # type: ignore[arg-type]
    with pytest.raises(ChallengerUnavailableError, match="disagrees"):
        embed_texts(["toxic politics"], cache, FakeProvider(drift=5.0))  # type: ignore[arg-type]


def test_too_many_gaps_or_no_gateway_skip_the_challenger() -> None:
    texts = [f"review {i}" for i in range(MAX_LIVE_EMBEDDINGS + 1)]
    with pytest.raises(ChallengerUnavailableError, match="k3s-text-embeddings"):
        embed_texts(texts, {}, FakeProvider())  # type: ignore[arg-type]
    with pytest.raises(ChallengerUnavailableError):
        embed_texts(["one"], {}, None)
    other_model = _store_with_cache(["a"], hf_model_id="other/model")
    assert load_embedding_cache(other_model, "demo", CHALLENGER) == {}  # type: ignore[arg-type]


def test_with_embeddings_replaces_the_text_column() -> None:
    x = pd.DataFrame({"pay": [3, 4], "Review": ["a", "b"]}, index=[10, 11])
    out = with_embeddings(x, "Review", np.ones((2, 3), dtype=np.float32))
    assert list(out.columns) == ["pay", "Review__emb_0", "Review__emb_1", "Review__emb_2"]
    assert list(out.index) == [10, 11]


def test_train_text_challenger_on_the_champions_split() -> None:
    rng = np.random.default_rng(0)
    n = 240
    y = pd.Series(rng.integers(0, 2, size=n), name="bad")
    x = pd.DataFrame({
        "pay": rng.integers(1, 6, size=n).astype(float),
        "family": pd.Categorical(rng.choice(["Data", "Software"], size=n)),
        "Review": [f"{rng.choice(TOXIC) if t else rng.choice(GOOD)} {i}" for i, t in enumerate(y)],
    })
    store = _store_with_cache(list(x["Review"]))
    train, test = x.index[:192], x.index[192:]

    result = train_text_challenger(
        CHALLENGER,
        store=store,  # type: ignore[arg-type]
        org_id="demo",
        provider=FakeProvider(),  # type: ignore[arg-type]
        x=x,
        y=y,
        train_index=train,
        test_index=test,
        numeric_features=["pay"],
        categorical_features=["family"],
        text_features=["Review"],
        feature_columns=["pay", "family", "Review"],
        task_type="classification",
        selection_metric="roc_auc",
        cv_folds=3,
    )

    assert result.metadata["embedding_dim"] == 4
    assert result.metadata["input_columns"][-4:] == [f"Review__emb_{i}" for i in range(4)]
    assert result.metadata["metrics"]["cv_roc_auc_mean"] > 0.9
    assert result.metadata["global_feature_importance"][0]["feature"] == "Review"
    assert result.metadata["holdout_evaluation"]["n"] == 48
