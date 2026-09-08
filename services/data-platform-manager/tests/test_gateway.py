"""Tests for core.gateway.get_rate_limits — the read-only llm-gateway rate-limit
report. Same fake-httpx-client mocking style as platform-registry's own
test_llm_settings.py, since both talk to the same /model/info admin endpoint.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from data_platform_manager.core import gateway


class _FakeResponse:
    """Minimal stand-in for httpx.Response, covering only what get_rate_limits reads."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://llm-gateway:4000/model/info")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self) -> Any:
        return self._payload


class _FakeHttpClient:
    """Minimal stand-in for httpx.Client — covers only .get plus the context manager protocol."""

    def __init__(self, get_response: _FakeResponse) -> None:
        self._get_response = get_response

    def __enter__(self) -> _FakeHttpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get(self, _path: str) -> _FakeResponse:
        return self._get_response


def test_get_rate_limits_extracts_rpm_and_tpm_per_model(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "data": [
            {"model_name": "claude-haiku", "litellm_params": {"rpm": 50, "tpm": 100000}},
            {"model_name": "gpt-4o-mini", "litellm_params": {"rpm": 60, "tpm": 150000}},
        ]
    }
    fake_client = _FakeHttpClient(_FakeResponse(payload))
    monkeypatch.setattr(gateway, "_client", lambda base_url, master_key: fake_client)

    result = gateway.get_rate_limits("http://llm-gateway:4000", "master-key")

    assert result == [
        {"model_name": "claude-haiku", "rpm": 50, "tpm": 100000},
        {"model_name": "gpt-4o-mini", "rpm": 60, "tpm": 150000},
    ]


def test_get_rate_limits_reports_none_for_an_unset_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"data": [{"model_name": "local-embed", "litellm_params": {}}]}
    fake_client = _FakeHttpClient(_FakeResponse(payload))
    monkeypatch.setattr(gateway, "_client", lambda base_url, master_key: fake_client)

    result = gateway.get_rate_limits("http://llm-gateway:4000", "master-key")

    assert result == [{"model_name": "local-embed", "rpm": None, "tpm": None}]


def test_get_rate_limits_sorts_by_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"data": [{"model_name": "zeta", "litellm_params": {}}, {"model_name": "alpha", "litellm_params": {}}]}
    fake_client = _FakeHttpClient(_FakeResponse(payload))
    monkeypatch.setattr(gateway, "_client", lambda base_url, master_key: fake_client)

    result = gateway.get_rate_limits("http://llm-gateway:4000", "master-key")

    assert [r["model_name"] for r in result] == ["alpha", "zeta"]


def test_get_rate_limits_raises_llm_gateway_error_on_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeHttpClient(_FakeResponse({}, status_code=500))
    monkeypatch.setattr(gateway, "_client", lambda base_url, master_key: fake_client)

    with pytest.raises(gateway.LlmGatewayError):
        gateway.get_rate_limits("http://llm-gateway:4000", "master-key")
