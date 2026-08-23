"""Tests for the pluggable embedding providers."""

from __future__ import annotations

import httpx
import pytest

from ai_circus_shared import embeddings
from ai_circus_shared.embeddings import (
    GatewayEmbeddingProvider,
    GeminiEmbeddingProvider,
    VoyageEmbeddingProvider,
    build_embedding_provider,
)


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


def test_gateway_embedding_provider_hits_llm_gateway_embeddings_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """encode_documents/encode_query both POST /embeddings with the configured model_name and Bearer auth."""
    calls: list[httpx.Request] = []
    seen_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        calls.append(request)
        body = json.loads(request.content)
        seen_bodies.append(body)
        vectors = [[float(i), float(i) + 1] for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": [{"embedding": v} for v in vectors]})

    _mock_httpx_client(monkeypatch, handler)

    provider = GatewayEmbeddingProvider("http://llm-gateway:4000", "fake-key", "local-embed")

    assert provider.dimension == 2  # from the constructor's probe call
    assert provider.encode_documents(["doc a", "doc b"]) == [[0.0, 1.0], [1.0, 2.0]]
    assert provider.encode_query("a query") == [0.0, 1.0]
    assert all(r.headers["authorization"] == "Bearer fake-key" for r in calls)
    assert all(body["model"] == "local-embed" for body in seen_bodies)


def test_build_embedding_provider_defaults_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider='local' builds a GatewayEmbeddingProvider using DEFAULT_LOCAL_MODEL when no override is given."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    _mock_httpx_client(monkeypatch, handler)

    provider = build_embedding_provider("local", None, None, None, "http://llm-gateway:4000", "fake-key")

    assert isinstance(provider, GatewayEmbeddingProvider)
    assert provider._model_name == embeddings.DEFAULT_LOCAL_MODEL  # type: ignore[attr-defined]


def test_build_embedding_provider_local_requires_gateway_config() -> None:
    """Selecting local without LLM_GATEWAY_URL/LLM_GATEWAY_API_KEY fails fast, not with a confusing connection error."""
    with pytest.raises(RuntimeError, match="LLM_GATEWAY_URL"):
        build_embedding_provider("local", None, None, None, None, None)


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
