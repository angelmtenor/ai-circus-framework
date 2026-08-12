"""Tests for the assistant FastAPI app: /healthz, /model/{scenario_slug}, the
/agui/{scenario_slug} streaming endpoint's auth/routing (not its full event
stream — that's exercised against a real LLM via Docker, see the phase-3 plan's
verification section), and the _llm_model dependency.
"""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace

import httpx
import pytest
from ag_ui.core import RunAgentInput
from ai_circus_shared.auth import Identity
from ai_circus_shared.entitlements import PlatformRegistryClient
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from assistant import api as api_module
from assistant.api import _llm_model, _prompt_cache, _scenario_definition, agui_endpoint, router
from assistant.core.identity import resolve_identity
from tests.conftest import FakeSecret


@pytest.fixture
def client() -> Generator[TestClient]:
    """A TestClient with identity/prompt-cache dependencies overridden by fakes."""
    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})
    )
    app.dependency_overrides[_scenario_definition] = lambda: SimpleNamespace(slug="churn")
    app.dependency_overrides[_prompt_cache] = lambda: SimpleNamespace(get=lambda _org_id, _slug: "system prompt")
    app.dependency_overrides[_llm_model] = lambda: "gpt-4o-mini"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_healthz(client: TestClient) -> None:
    """/healthz reports ok."""
    assert client.get("/healthz").json() == {"status": "ok"}


def test_model_endpoint_returns_the_active_model_without_sending_a_message(client: TestClient) -> None:
    """GET /model/{scenario_slug} lets the UI show the model before the first chat turn."""
    response = client.get("/model/churn")

    assert response.status_code == 200
    assert response.json() == {"model": "gpt-4o-mini"}


def test_agui_unknown_scenario_returns_404() -> None:
    """A scenario_slug this instance doesn't serve 404s, distinct from an auth failure — same
    `_scenario_definition` dependency as every other route, exercised for real (not overridden).
    """
    app = FastAPI()
    app.include_router(router)
    app.state.definitions = {}  # exercises the real _scenario_definition lookup, not an override
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="user-1", org_id="org-1", roles=frozenset())
    client = TestClient(app)

    response = client.post(
        "/agui/does-not-exist",
        json={"threadId": "t", "runId": "r", "messages": [], "tools": [], "context": [], "state": {}},
    )

    assert response.status_code == 404


class _FakeLlmEnvConfig:
    """Minimal stand-in for EnvConfig, covering only what _llm_model() reads."""

    PLATFORM_REGISTRY_URL = "http://platform-registry:8000"
    ADMIN_API_KEY = FakeSecret("admin-secret")
    LLM_MODEL = "llama3"


def test_llm_model_uses_platform_registrys_live_active_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Settings page's live picker wins over the instance's static LLM_MODEL default."""
    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(PlatformRegistryClient, "get_active_llm_model", lambda self, *, admin_api_key: "gemini-flash")

    assert _llm_model() == "gemini-flash"


def test_llm_model_falls_back_to_static_default_when_platform_registry_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform-registry hiccup shouldn't break chat — fall back to the static LLM_MODEL."""

    def _raise(self: PlatformRegistryClient, *, admin_api_key: str) -> str:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(PlatformRegistryClient, "get_active_llm_model", _raise)

    assert _llm_model() == "llama3"


def _fake_http_request(authorization: str | None) -> Request:
    """A minimal real Request (not a mock) so `request.headers.get(...)` behaves exactly
    like it does in production, without wiring up a full ASGI call.
    """
    headers = [(b"accept", b"application/json")]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request({"type": "http", "headers": headers})


class _FakePredictionEnvConfig:
    """Minimal stand-in for EnvConfig, covering only what agui_endpoint reads for tool wiring."""

    PREDICTION_SERVICE_URL = "http://prediction:8000"


async def test_agui_endpoint_builds_prediction_tools_scoped_to_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent's tools are built for this scenario_slug and the caller's forwarded
    Authorization header — not a shared/global client.
    """
    captured: dict[str, object] = {}

    def fake_build_prediction_tools(client: object, *, scenario_slug: str, authorization: str | None) -> list[str]:
        captured["base_url"] = client.base_url  # type: ignore[attr-defined]
        captured["scenario_slug"] = scenario_slug
        captured["authorization"] = authorization
        return ["prediction-tool-sentinel"]

    def fake_build_agui_agent(llm: object, system_prompt: str, tools: list[str]) -> str:
        captured["tools"] = tools
        return "fake-graph"

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakePredictionEnvConfig())
    monkeypatch.setattr(api_module, "build_prediction_tools", fake_build_prediction_tools)
    monkeypatch.setattr(api_module, "build_agui_agent", fake_build_agui_agent)
    monkeypatch.setattr(
        api_module, "LangGraphAGUIAgent", lambda *, name, graph: SimpleNamespace(run=lambda _input: iter(()))
    )

    await agui_endpoint(
        scenario_slug="motor_speed",
        input_data=RunAgentInput(
            threadId="t", runId="r", messages=[], tools=[], context=[], state={}, forwardedProps={}
        ),
        request=_fake_http_request("Bearer tok-1"),
        identity=Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:motor_speed"})),
        definition=SimpleNamespace(slug="motor_speed"),
        prompt_cache=SimpleNamespace(get=lambda _org_id, _slug: "system prompt"),
        llm=SimpleNamespace(),
    )

    assert captured["base_url"] == "http://prediction:8000"
    assert captured["scenario_slug"] == "motor_speed"
    assert captured["authorization"] == "Bearer tok-1"
    assert captured["tools"] == ["prediction-tool-sentinel"]
