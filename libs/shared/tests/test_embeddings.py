"""Tests for the pluggable embedding providers."""

from __future__ import annotations

import sys
import types

import httpx
import pytest

from ai_circus_shared import embeddings
from ai_circus_shared.embeddings import (
    GeminiEmbeddingProvider,
    LocalEmbeddingProvider,
    VoyageEmbeddingProvider,
    build_embedding_provider,
)


class _FakeSentenceTransformer:
    """Deterministic stand-in for sentence_transformers.SentenceTransformer."""

    def __init__(self, model_name: str) -> None:
        """Record the model name it was constructed with."""
        self.model_name = model_name

    def get_sentence_embedding_dimension(self) -> int:
        """Report a small fixed vector size."""
        return 4

    def encode(self, texts: str | list[str], normalize_embeddings: bool = True) -> list:
        """Return one fixed vector per input (or a single vector for a single string)."""
        if isinstance(texts, str):
            return [0.1, 0.2, 0.3, 0.4]
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


@pytest.fixture
def fake_sentence_transformers_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake sentence_transformers module so LocalEmbeddingProvider's lazy
    import resolves to a deterministic fake regardless of whether the real (heavy,
    torch-backed) package is installed in this environment.
    """
    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = _FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)


def test_local_embedding_provider_reports_dimension_and_encodes(fake_sentence_transformers_module: None) -> None:
    """LocalEmbeddingProvider reads the model's dimension and normalizes encode() output to plain floats."""
    provider = LocalEmbeddingProvider("fake-model")

    assert provider.dimension == 4
    assert provider.encode_documents(["a", "b"]) == [[0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3, 0.4]]
    assert provider.encode_query("q") == [0.1, 0.2, 0.3, 0.4]


def _mock_httpx_client(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    """Redirect embeddings.httpx.Client construction to a MockTransport-backed client,
    preserving base_url/headers so handlers/assertions can still see them.
    """

    real_client_cls = httpx.Client

    def fake_client(**kwargs: object) -> httpx.Client:
        return real_client_cls(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
            base_url=kwargs.get("base_url", ""),  # type: ignore[arg-type]
            headers=kwargs.get("headers"),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(embeddings.httpx, "Client", fake_client)


def test_gemini_embedding_provider_encodes_documents_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """encode_documents hits batchEmbedContents, encode_query hits embedContent, and the
    dimension is inferred from a live probe call rather than hardcoded.
    """
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if str(request.url).endswith(":batchEmbedContents"):
            return httpx.Response(200, json={"embeddings": [{"values": [1.0, 2.0]}, {"values": [3.0, 4.0]}]})
        return httpx.Response(200, json={"embedding": {"values": [0.5, 0.6]}})

    _mock_httpx_client(monkeypatch, handler)

    provider = GeminiEmbeddingProvider("fake-key", "gemini-embedding-001")

    assert provider.dimension == 2  # from the constructor's probe call
    assert provider.encode_documents(["doc a", "doc b"]) == [[1.0, 2.0], [3.0, 4.0]]
    assert provider.encode_query("a query") == [0.5, 0.6]
    assert all(r.headers["x-goog-api-key"] == "fake-key" for r in calls)


def test_voyage_embedding_provider_encodes_documents_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """encode_documents/encode_query both hit /v1/embeddings with input_type=document|query."""
    seen_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        seen_bodies.append(body)
        vectors = [[float(i), float(i) + 1] for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": [{"embedding": v} for v in vectors]})

    _mock_httpx_client(monkeypatch, handler)

    provider = VoyageEmbeddingProvider("fake-key", "voyage-3.5-lite")

    assert provider.dimension == 2  # from the constructor's probe call
    assert provider.encode_documents(["doc a", "doc b"]) == [[0.0, 1.0], [1.0, 2.0]]
    assert provider.encode_query("a query") == [0.0, 1.0]
    assert seen_bodies[-1]["input_type"] == "query"
    assert seen_bodies[0]["input_type"] == "query"  # the constructor's own probe call


def test_build_embedding_provider_defaults_to_local(
    monkeypatch: pytest.MonkeyPatch, fake_sentence_transformers_module: None
) -> None:
    """provider='local' builds a LocalEmbeddingProvider using DEFAULT_LOCAL_MODEL when no override is given."""
    provider = build_embedding_provider("local", None, None, None)

    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider._model.model_name == embeddings.DEFAULT_LOCAL_MODEL  # type: ignore[attr-defined]


def test_build_embedding_provider_gemini_requires_api_key() -> None:
    """Selecting gemini without GOOGLE_API_KEY fails fast with a clear error, not a confusing 401 later."""
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        build_embedding_provider("gemini", None, None, None)


def test_build_embedding_provider_voyage_requires_api_key() -> None:
    """Selecting voyage without VOYAGE_API_KEY fails fast with a clear error, not a confusing 401 later."""
    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY"):
        build_embedding_provider("voyage", None, None, None)


def test_build_embedding_provider_rejects_unknown_provider() -> None:
    """An unrecognized EMBEDDING_PROVIDER value fails fast rather than silently picking a default."""
    with pytest.raises(ValueError, match="Unknown EMBEDDING_PROVIDER"):
        build_embedding_provider("bogus", None, None, None)
