"""Tests for llm_settings.list_providers/test_provider/test_all_providers — in
particular the nested provider -> models shape, since GroqCloud routes two models
(groq-llama, groq-oss-20b) sharing one API key.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from platform_registry.core import llm_settings


class _FakeResponse:
    """Minimal stand-in for httpx.Response, covering only what list_providers/test_provider read."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://llm-gateway:4000/model/info")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self) -> Any:
        return self._payload


class _FakeHttpClient:
    """Minimal stand-in for httpx.Client — covers only .get/.post plus the context
    manager protocol `_client()`'s callers use.
    """

    def __init__(
        self,
        get_response: _FakeResponse | None = None,
        post_response: _FakeResponse | None = None,
        post_error: Exception | None = None,
    ) -> None:
        self._get_response = get_response
        self._post_response = post_response
        self._post_error = post_error
        self.post_calls: list[dict[str, Any]] = []

    def __enter__(self) -> _FakeHttpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get(self, _path: str) -> _FakeResponse:
        assert self._get_response is not None
        return self._get_response

    def post(self, _path: str, *, json: dict[str, Any], timeout: float) -> _FakeResponse:
        self.post_calls.append(json)
        if self._post_error is not None:
            raise self._post_error
        assert self._post_response is not None
        return self._post_response


def test_list_providers_nests_models_per_provider_and_reports_route_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """GroqCloud's two models are nested under one provider entry, each with its own
    live routing status — a model litellm doesn't currently route is `route_exists=False`.
    """
    deployments = {
        "data": [
            {"model_name": "gpt-4o-mini", "litellm_params": {"model": "openai/gpt-4o-mini"}},
            {"model_name": "groq-llama", "litellm_params": {"model": "groq/openai/gpt-oss-120b"}},
            # groq-oss-20b deliberately absent — simulates a not-yet-routed model.
        ]
    }
    fake_client = _FakeHttpClient(get_response=_FakeResponse(deployments))
    monkeypatch.setattr(llm_settings, "_client", lambda base_url, master_key: fake_client)

    providers = llm_settings.list_providers("http://llm-gateway:4000", "master-key")

    groq = next(p for p in providers if p["provider"] == "groq")
    models_by_name = {m["model_name"]: m for m in groq["models"]}
    assert models_by_name["groq-llama"]["route_exists"] is True
    assert models_by_name["groq-llama"]["model"] == "openai/gpt-oss-120b"
    assert models_by_name["groq-oss-20b"]["route_exists"] is False
    assert models_by_name["groq-oss-20b"]["model"] is None

    openai = next(p for p in providers if p["provider"] == "openai")
    assert len(openai["models"]) == 1
    assert openai["models"][0]["route_exists"] is True


def test_list_providers_raises_llm_gateway_error_on_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable llm-gateway raises a typed error, not a raw httpx exception."""
    fake_client = _FakeHttpClient(get_response=_FakeResponse({}, status_code=502))
    monkeypatch.setattr(llm_settings, "_client", lambda base_url, master_key: fake_client)

    with pytest.raises(llm_settings.LlmGatewayError):
        llm_settings.list_providers("http://llm-gateway:4000", "master-key")


def test_test_provider_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unknown provider"):
        llm_settings.test_provider("http://llm-gateway:4000", "master-key", "not-a-provider", "whatever")


def test_test_provider_rejects_a_model_not_belonging_to_the_provider() -> None:
    """groq-oss-20b belongs to "groq", not "openai" — testing it through the wrong
    provider is rejected rather than silently routed through the right one anyway.
    """
    with pytest.raises(ValueError, match="does not belong to provider"):
        llm_settings.test_provider("http://llm-gateway:4000", "master-key", "openai", "groq-oss-20b")


def test_test_provider_reports_a_successful_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeHttpClient(
        post_response=_FakeResponse({"choices": [{"message": {"content": "ok"}}]}),
    )
    monkeypatch.setattr(llm_settings, "_client", lambda base_url, master_key: fake_client)

    result = llm_settings.test_provider("http://llm-gateway:4000", "master-key", "groq", "groq-oss-20b")

    assert result == {"ok": True, "error": None, "latency_ms": pytest.approx(0, abs=10_000), "reply": "ok"}
    assert fake_client.post_calls[0]["model"] == "groq-oss-20b"


def test_test_provider_reports_a_provider_error_response(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeHttpClient(
        post_response=_FakeResponse({"error": {"message": "rate_limit_exceeded"}}, status_code=429),
    )
    monkeypatch.setattr(llm_settings, "_client", lambda base_url, master_key: fake_client)

    result = llm_settings.test_provider("http://llm-gateway:4000", "master-key", "groq", "groq-llama")

    assert result["ok"] is False
    assert result["error"] == "rate_limit_exceeded"


def test_test_provider_reports_a_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeHttpClient(post_error=httpx.ConnectError("connection refused"))
    monkeypatch.setattr(llm_settings, "_client", lambda base_url, master_key: fake_client)

    result = llm_settings.test_provider("http://llm-gateway:4000", "master-key", "ollama", "llama3")

    assert result == {"ok": False, "error": "connection refused", "latency_ms": None}


def test_test_all_providers_covers_every_model_of_every_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """One result per (provider, model_name) pair across all of PROVIDERS — in
    particular both of GroqCloud's models, not just one per provider.
    """
    calls: list[tuple[str, str]] = []

    def fake_test_provider(_base_url: str, _master_key: str, provider: str, model_name: str) -> dict[str, object]:
        calls.append((provider, model_name))
        return {"ok": True, "error": None, "latency_ms": 1.0, "reply": "ok"}

    monkeypatch.setattr(llm_settings, "test_provider", fake_test_provider)

    results = llm_settings.test_all_providers("http://llm-gateway:4000", "master-key")

    expected = {(spec.key, model.model_name) for spec in llm_settings.PROVIDERS.values() for model in spec.models}
    assert set(calls) == expected
    assert results["groq"]["groq-llama"]["ok"] is True
    assert results["groq"]["groq-oss-20b"]["ok"] is True


def test_find_model_locates_the_owning_provider_and_model() -> None:
    found = llm_settings.find_model("groq-oss-20b")

    assert found is not None
    spec, model = found
    assert spec.key == "groq"
    assert model.model_name == "groq-oss-20b"


def test_find_model_returns_none_for_an_unrouted_alias() -> None:
    assert llm_settings.find_model("not-a-real-model") is None
