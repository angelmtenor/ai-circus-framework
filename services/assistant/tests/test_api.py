"""Tests for the /chat FastAPI endpoint, with identity/prompt-cache/llm-client dependencies overridden."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from ai_circus_shared.auth import Identity
from ai_circus_shared.entitlements import PlatformRegistryClient
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistant import api as api_module
from assistant.api import _llm_client, _llm_model, _prompt_cache, _scenario_definition, router
from assistant.core.identity import resolve_identity
from tests.conftest import FakeSecret


@pytest.fixture
def fake_llm_client() -> MagicMock:
    """A mock OpenAI client returning a fixed completion."""
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Age is the strongest predictor."))]
    )
    return client


@pytest.fixture
def client(fake_llm_client: MagicMock) -> Generator[TestClient]:
    """A TestClient with identity/prompt-cache/llm-client dependencies overridden by fakes."""
    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})
    )
    app.dependency_overrides[_scenario_definition] = lambda: SimpleNamespace(slug="churn")
    app.dependency_overrides[_prompt_cache] = lambda: SimpleNamespace(get=lambda _org_id, _slug: "system prompt")
    app.dependency_overrides[_llm_client] = lambda: fake_llm_client
    app.dependency_overrides[_llm_model] = lambda: "gpt-4o-mini"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_healthz(client: TestClient) -> None:
    """/healthz reports ok."""
    assert client.get("/healthz").json() == {"status": "ok"}


def test_chat_returns_reply(client: TestClient, fake_llm_client: MagicMock) -> None:
    """POST /chat/{scenario_slug} returns the completion's reply text."""
    response = client.post("/chat/churn", json={"message": "what matters most?"})

    assert response.status_code == 200
    assert response.json() == {"reply": "Age is the strongest predictor."}
    _, kwargs = fake_llm_client.chat.completions.create.call_args
    assert kwargs["messages"][0] == {"role": "system", "content": "system prompt"}
    assert kwargs["messages"][-1] == {"role": "user", "content": "what matters most?"}


def test_chat_forwards_history(client: TestClient, fake_llm_client: MagicMock) -> None:
    """Prior conversation turns are forwarded to the completion call in order."""
    response = client.post(
        "/chat/churn",
        json={
            "message": "and then?",
            "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    _, kwargs = fake_llm_client.chat.completions.create.call_args
    assert kwargs["messages"][1:3] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


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
