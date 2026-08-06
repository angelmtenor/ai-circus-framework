"""Tests for PlatformRegistryClient.get_active_llm_model."""

from __future__ import annotations

import httpx
import pytest

from ai_circus_shared import entitlements as entitlements_module
from ai_circus_shared.entitlements import PlatformRegistryClient


class _FakeResponse:
    """Minimal stand-in for httpx.Response, covering only what get_active_llm_model reads."""

    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        """Store the fake JSON payload and status code this response should report."""
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """Raise HTTPStatusError for non-2xx status codes, like the real httpx.Response."""
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://platform-registry:8000/llm-settings/active-model")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self) -> dict[str, object]:
        """Return the fake JSON payload."""
        return self._payload


def test_get_active_llm_model_sends_admin_bearer_and_parses_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The call hits platform-registry's active-model endpoint with the admin bearer token."""
    captured: dict[str, object] = {}

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResponse(payload={"model_name": "gemini-flash"})

    monkeypatch.setattr(entitlements_module.httpx, "get", fake_get)
    client = PlatformRegistryClient(base_url="http://platform-registry:8000")

    result = client.get_active_llm_model(admin_api_key="secret-key")

    assert result == "gemini-flash"
    assert captured["url"] == "http://platform-registry:8000/llm-settings/active-model"
    assert captured["headers"] == {"Authorization": "Bearer secret-key"}


def test_get_active_llm_model_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-2xx response propagates as an HTTPError — callers decide whether to fall back."""
    monkeypatch.setattr(entitlements_module.httpx, "get", lambda *_a, **_kw: _FakeResponse(payload={}, status_code=404))
    client = PlatformRegistryClient(base_url="http://platform-registry:8000")

    with pytest.raises(httpx.HTTPStatusError):
        client.get_active_llm_model(admin_api_key="secret-key")
