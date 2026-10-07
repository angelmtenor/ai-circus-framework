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
        if "" in texts and len(set(texts)) == 1:
            raise RuntimeError("cannot reshape tensor of 0 elements")  # voyage-4-nano on an all-empty batch
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    def get_sentence_embedding_dimension(self) -> int:
        """Like voyage-4-nano: the backbone's width, not encode()'s (must not be trusted)."""
        return 8


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


def test_embedding_answers_empty_texts_with_zero_vectors() -> None:
    """An empty text is zero tokens for some tokenizers: an all-empty batch must not reach
    the model, and an empty text gets the zero vector wherever it sits (what the training caches hold).
    """
    alone = custom_handler.local_embedding_llm.embedding(**_call_kwargs("fake-model", ["", ""]))
    mixed = custom_handler.local_embedding_llm.embedding(**_call_kwargs("fake-model", ["a", "", "b"]))

    assert [d["embedding"] for d in alone.data] == [[0.0] * 4, [0.0] * 4]
    assert [d["embedding"] for d in mixed.data] == [[0.1, 0.2, 0.3, 0.4], [0.0] * 4, [0.1, 0.2, 0.3, 0.4]]
    assert [d["index"] for d in mixed.data] == [0, 1, 2]


def test_aembedding_delegates_to_embedding() -> None:
    """aembedding() (litellm's async entry point) produces the same result as the sync one."""
    response = asyncio.run(custom_handler.local_embedding_llm.aembedding(**_call_kwargs("fake-model", ["a"])))

    assert response.data == [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}]


def test_aembedding_runs_the_cpu_bound_encode_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """LiteLLM serves every chat completion from one event loop — a multi-second local
    encode must run on a worker thread, not block that loop.
    """
    ran_on_loop: list[bool] = []

    def encode(
        self: _FakeSentenceTransformer, texts: list[str], normalize_embeddings: bool = True
    ) -> list[list[float]]:
        try:
            asyncio.get_running_loop()
            ran_on_loop.append(True)
        except RuntimeError:
            ran_on_loop.append(False)
        return [[0.0] for _ in texts]

    monkeypatch.setattr(_FakeSentenceTransformer, "encode", encode)
    asyncio.run(custom_handler.local_embedding_llm.aembedding(**_call_kwargs("fake-model", ["a"])))

    assert ran_on_loop == [False]


class _CountingSentenceTransformer(_FakeSentenceTransformer):
    """Counts encode() calls, to prove preload() warms the model and doesn't just build it."""

    encodes = 0

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> list[list[float]]:
        """Count, then behave like the plain fake."""
        type(self).encodes += 1
        return super().encode(texts, normalize_embeddings)


def test_preload_loads_and_warms_the_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.modules["sentence_transformers"].SentenceTransformer = _CountingSentenceTransformer  # type: ignore[attr-defined]
    _CountingSentenceTransformer.encodes = 0
    custom_handler.preload()
    assert custom_handler.DEFAULT_MODEL in custom_handler._loaded_models
    assert _CountingSentenceTransformer.encodes == 1
    # A later real call reuses the warm instance instead of loading again.
    loaded = custom_handler._loaded_models[custom_handler.DEFAULT_MODEL]
    assert custom_handler._get_model(custom_handler.DEFAULT_MODEL) is loaded


@pytest.mark.parametrize(
    ("preload", "provider", "expected"),
    [
        ("true", "local", True),
        ("true", None, True),  # unset provider = the platform default, local
        ("true", "gemini", False),  # the model would never be used
        ("false", "local", False),  # local dev / tests: stay lazy
        (None, "local", False),
    ],
)
def test_preload_enabled(
    monkeypatch: pytest.MonkeyPatch, preload: str | None, provider: str | None, expected: bool
) -> None:
    fake_config = types.SimpleNamespace(LOCAL_EMBED_PRELOAD=preload, EMBEDDING_PROVIDER=provider)
    monkeypatch.setattr(custom_handler, "get_env_config", lambda: fake_config)
    assert custom_handler.preload_enabled() is expected


def test_docker_profile_preloads_and_local_profile_does_not() -> None:
    """Compose and k8s both run APP_ENVIRONMENT=docker — the profile that must preload."""
    from llm_gateway.data_model import get_env_config

    assert get_env_config("docker").LOCAL_EMBED_PRELOAD == "true"
    assert get_env_config("local").LOCAL_EMBED_PRELOAD in {None, "false"}
