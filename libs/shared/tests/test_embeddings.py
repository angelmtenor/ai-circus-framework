"""Tests for the pluggable embedding providers."""

from __future__ import annotations

import httpx
import pytest

from ai_circus_shared import embeddings
from ai_circus_shared.embeddings import (
    GatewayEmbeddingProvider,
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


def _echo_vectors(seen_bodies: list[dict[str, object]], seen_hosts: list[str]) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        seen_bodies.append(body)
        seen_hosts.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"data": [{"embedding": [float(i), 1.0]} for i in range(len(body["input"]))]})

    return handler


def test_gemini_embeddings_go_through_the_gateway_with_retrieval_task_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider='gemini' calls llm-gateway's /embeddings (never Google directly), tagging documents and
    queries with the task types Gemini distinguishes; the dimension still comes from a live probe.
    """
    bodies: list[dict[str, object]] = []
    auth: list[str] = []
    _mock_httpx_client(monkeypatch, _echo_vectors(bodies, auth))

    provider = build_embedding_provider("gemini", None, "ignored", None, "http://llm-gateway:4000", "gw-key")

    assert isinstance(provider, GatewayEmbeddingProvider)
    assert provider.dimension == 2
    provider.encode_documents(["doc a", "doc b"])
    provider.encode_query("a query")
    assert {b["model"] for b in bodies} == {embeddings.DEFAULT_GEMINI_MODEL}
    assert bodies[-2]["task_type"] == "RETRIEVAL_DOCUMENT" and bodies[-1]["task_type"] == "RETRIEVAL_QUERY"
    assert set(auth) == {"Bearer gw-key"}


def test_voyage_embeddings_go_through_the_gateway_with_input_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider='voyage' calls llm-gateway's /embeddings with input_type=document|query."""
    bodies: list[dict[str, object]] = []
    _mock_httpx_client(monkeypatch, _echo_vectors(bodies, []))

    provider = build_embedding_provider("voyage", "voyage-3.5", None, "ignored", "http://llm-gateway:4000", "gw-key")

    provider.encode_documents(["doc a"])
    provider.encode_query("a query")
    assert {b["model"] for b in bodies} == {"voyage-3.5"}
    assert bodies[-2]["input_type"] == "document" and bodies[-1]["input_type"] == "query"


def test_local_embeddings_send_no_provider_specific_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, object]] = []
    _mock_httpx_client(monkeypatch, _echo_vectors(bodies, []))

    build_embedding_provider("local", None, None, None, "http://llm-gateway:4000", "gw-key").encode_query("q")

    assert all(set(b) == {"model", "input"} for b in bodies)


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
    """Selecting gemini without a gateway fails fast, naming the key the gateway needs."""
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
