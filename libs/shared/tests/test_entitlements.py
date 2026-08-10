"""Tests for PlatformRegistryClient.get_active_llm_model."""

from __future__ import annotations

import httpx
import pytest

from ai_circus_shared import entitlements as entitlements_module
from ai_circus_shared.entitlements import EntitlementDeniedError, PlatformRegistryClient


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """Every test starts with a cold cache — otherwise a later test could see an
    earlier test's cached (base_url-keyed) result instead of exercising its own fake.
    """
    entitlements_module._entitlement_cache.clear()
    entitlements_module._active_model_cache.clear()


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


def test_check_entitlement_caches_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second check_entitlement() for the same (base_url, org, scenario) doesn't
    re-hit platform-registry within the TTL window — the hot path on every request.
    """
    call_count = 0

    def fake_get(*_a: object, **_kw: object) -> _FakeResponse:
        nonlocal call_count
        call_count += 1
        return _FakeResponse(payload={}, status_code=200)

    monkeypatch.setattr(entitlements_module.httpx, "get", fake_get)
    client = PlatformRegistryClient(base_url="http://platform-registry:8000")

    client.check_entitlement(org_id="org-1", scenario_slug="churn")
    client.check_entitlement(org_id="org-1", scenario_slug="churn")

    assert call_count == 1


def test_check_entitlement_cache_is_scoped_per_org_and_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cached result for one (org, scenario) doesn't leak into a different pair."""
    monkeypatch.setattr(entitlements_module.httpx, "get", lambda *_a, **_kw: _FakeResponse(payload={}, status_code=200))
    client = PlatformRegistryClient(base_url="http://platform-registry:8000")
    client.check_entitlement(org_id="org-1", scenario_slug="churn")

    monkeypatch.setattr(entitlements_module.httpx, "get", lambda *_a, **_kw: _FakeResponse(payload={}, status_code=404))

    with pytest.raises(EntitlementDeniedError):
        client.check_entitlement(org_id="org-1", scenario_slug="mpm")


def test_check_entitlement_denied_raises_and_is_not_cached_as_entitled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 (not entitled) raises, and doesn't get cached as if it were a success."""
    monkeypatch.setattr(entitlements_module.httpx, "get", lambda *_a, **_kw: _FakeResponse(payload={}, status_code=404))
    client = PlatformRegistryClient(base_url="http://platform-registry:8000")

    with pytest.raises(EntitlementDeniedError):
        client.check_entitlement(org_id="org-1", scenario_slug="churn")
    with pytest.raises(EntitlementDeniedError):
        client.check_entitlement(org_id="org-1", scenario_slug="churn")


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


def test_get_active_llm_model_caches_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second get_active_llm_model() for the same base_url doesn't re-hit
    platform-registry within the TTL window — the hot path on every chat request.
    """
    call_count = 0

    def fake_get(*_a: object, **_kw: object) -> _FakeResponse:
        nonlocal call_count
        call_count += 1
        return _FakeResponse(payload={"model_name": "gemini-flash"})

    monkeypatch.setattr(entitlements_module.httpx, "get", fake_get)
    client = PlatformRegistryClient(base_url="http://platform-registry:8000")

    first = client.get_active_llm_model(admin_api_key="secret-key")
    second = client.get_active_llm_model(admin_api_key="secret-key")

    assert first == second == "gemini-flash"
    assert call_count == 1
