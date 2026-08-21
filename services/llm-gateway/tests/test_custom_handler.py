"""Tests for the local sentence-transformers embedding provider (custom_handler.py)."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest
from litellm.types.utils import EmbeddingResponse

from llm_gateway import custom_handler


class _FakeSentenceTransformer:
    """Deterministic stand-in for sentence_transformers.SentenceTransformer."""

    def __init__(self, model_name: str) -> None:
        """Record the model name it was constructed with."""
        self.model_name = model_name

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> list[list[float]]:
        """Return one fixed vector per input text."""
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


@pytest.fixture(autouse=True)
def fake_sentence_transformers_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake sentence_transformers module and clear the model cache, so tests
    never depend on the real (heavy, torch-backed) package or leak state between runs.
    """
    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = _FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(custom_handler, "_loaded_models", {})


def _call_kwargs(model: str, input_texts: list[str]) -> dict[str, object]:
    return {
        "model": model,
        "input": input_texts,
        "model_response": EmbeddingResponse(data=[], object="list"),
        "print_verbose": lambda *_a, **_kw: None,
        "logging_obj": None,
        "optional_params": {},
    }


def test_embedding_encodes_input_and_caches_model() -> None:
    """embedding() loads the model named by `model`, normalizes vectors to plain floats, and reuses the cache."""
    response = custom_handler.local_embedding_llm.embedding(**_call_kwargs("fake-model", ["a", "b"]))

    assert response.data == [
        {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]},
        {"object": "embedding", "index": 1, "embedding": [0.1, 0.2, 0.3, 0.4]},
    ]
    assert response.model == "fake-model"
    assert response.usage.total_tokens == 0
    assert "fake-model" in custom_handler._loaded_models


def test_embedding_falls_back_to_default_model_when_model_is_empty() -> None:
    """An empty `model` (litellm's prefix-stripping can leave it so) falls back to DEFAULT_MODEL, not a crash."""
    response = custom_handler.local_embedding_llm.embedding(**_call_kwargs("", ["a"]))

    assert response.model == custom_handler.DEFAULT_MODEL
    assert custom_handler.DEFAULT_MODEL in custom_handler._loaded_models


def test_aembedding_delegates_to_embedding() -> None:
    """aembedding() (litellm's async entry point) produces the same result as the sync one."""
    response = asyncio.run(custom_handler.local_embedding_llm.aembedding(**_call_kwargs("fake-model", ["a"])))

    assert response.data == [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}]
