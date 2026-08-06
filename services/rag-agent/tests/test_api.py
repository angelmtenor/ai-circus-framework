"""Tests for the /chat/{scenario_slug} FastAPI endpoint, with all dependencies
overridden by fakes — including a fake tool-calling chat model, so these tests
exercise the real agent loop (see core/agent.py) rather than mocking it away.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from ai_circus_shared.auth import Identity
from ai_circus_shared.entitlements import PlatformRegistryClient
from ai_circus_shared.scenario_schema import ChatConfig, VectorStoreConfig
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult

from rag_agent import api as api_module
from rag_agent.api import _embedders, _llm, _qdrant, _scenario_definition, router
from rag_agent.core.identity import resolve_identity
from tests.conftest import FakeSecret


class FakeToolCallingModel(BaseChatModel):
    """A minimal fake chat model that supports tool binding and returns fixed responses in order."""

    responses: list[AIMessage]
    calls: int = 0

    def bind_tools(self, tools: object, **kwargs: object) -> FakeToolCallingModel:
        """Tool binding is a no-op here — the fake ignores the tool schema entirely."""
        return self

    def _generate(
        self, messages: object, stop: object = None, run_manager: object = None, **kwargs: object
    ) -> ChatResult:
        """Return the next canned response in sequence."""
        message = self.responses[self.calls]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        """Identify this fake model type for LangChain's internals."""
        return "fake-tool-calling-model"


def _client_with(llm: FakeToolCallingModel) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    fake_point = SimpleNamespace(
        payload={"text": "Overdraft fee is $25.", "source": "raw/account_policies.md"}, score=0.9
    )
    fake_definition = SimpleNamespace(
        slug="docs_rag",
        vector_store=VectorStoreConfig(backend="qdrant", collection_prefix="docs_rag", top_k=3),
        chat=ChatConfig(context="Bank account policies and fees."),
    )

    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:docs_rag"})
    )
    app.dependency_overrides[_scenario_definition] = lambda: fake_definition
    app.dependency_overrides[_qdrant] = lambda: SimpleNamespace(
        collection_exists=lambda _name: True,
        query_points=lambda **_kwargs: SimpleNamespace(points=[fake_point]),
    )
    app.dependency_overrides[_embedders] = lambda: {"docs_rag": SimpleNamespace(encode=lambda _q, **_kw: [0.1, 0.2])}
    app.dependency_overrides[_llm] = lambda: llm
    return TestClient(app)


def test_healthz() -> None:
    """/healthz reports ok."""
    client = _client_with(FakeToolCallingModel(responses=[]))
    assert client.get("/healthz").json() == {"status": "ok"}


def test_chat_calls_the_tool_for_an_in_domain_question_and_returns_sources() -> None:
    """POST /chat/{scenario_slug} retrieves and returns sources for an in-domain question."""
    tool_call = ToolCall(name="retrieve_docs", args={"query": "overdraft fee"}, id="call_1")
    llm = FakeToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[tool_call]),
            AIMessage(content="The overdraft fee is $25."),
        ]
    )
    client = _client_with(llm)

    response = client.post("/chat/docs_rag", json={"message": "what is the overdraft fee?"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "The overdraft fee is $25."
    assert body["sources"] == [{"source": "raw/account_policies.md", "score": 0.9}]


def test_chat_skips_the_tool_for_chitchat_and_returns_no_sources() -> None:
    """POST /chat/{scenario_slug} answers chitchat directly, without retrieval."""
    llm = FakeToolCallingModel(responses=[AIMessage(content="Hi there! How can I help?")])
    client = _client_with(llm)

    response = client.post("/chat/docs_rag", json={"message": "hi, how are you?"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Hi there! How can I help?"
    assert body["sources"] == []


def test_chat_unknown_scenario_returns_404() -> None:
    """A scenario_slug this instance doesn't serve 404s, distinct from an auth failure."""
    app = FastAPI()
    app.include_router(router)
    app.state.definitions = {}  # exercises the real _scenario_definition lookup, not an override
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="user-1", org_id="org-1", roles=frozenset())
    client = TestClient(app)

    response = client.post("/chat/does-not-exist", json={"message": "hi"})

    assert response.status_code == 404


class _FakeLlmEnvConfig:
    """Minimal stand-in for EnvConfig, covering only what _llm() reads."""

    PLATFORM_REGISTRY_URL = "http://platform-registry:8000"
    ADMIN_API_KEY = FakeSecret("admin-secret")
    LLM_MODEL = "llama3"
    LLM_GATEWAY_URL = "http://llm-gateway:4000"
    LLM_GATEWAY_API_KEY = FakeSecret("master-key")


def _fake_request() -> SimpleNamespace:
    """A minimal stand-in for FastAPI's Request, exposing only what _llm() reads."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_clients={})))


def test_llm_uses_platform_registrys_live_active_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Settings page's live picker wins over the instance's static LLM_MODEL default."""
    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(PlatformRegistryClient, "get_active_llm_model", lambda self, *, admin_api_key: "gemini-flash")

    llm = _llm(_fake_request())

    assert llm.model_name == "gemini-flash"


def test_llm_falls_back_to_static_default_when_platform_registry_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform-registry hiccup shouldn't break chat — fall back to the static LLM_MODEL."""

    def _raise(self: PlatformRegistryClient, *, admin_api_key: str) -> str:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(PlatformRegistryClient, "get_active_llm_model", _raise)

    llm = _llm(_fake_request())

    assert llm.model_name == "llama3"


def test_llm_caches_the_client_per_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeat calls for the same active model reuse one client instead of rebuilding it."""
    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(PlatformRegistryClient, "get_active_llm_model", lambda self, *, admin_api_key: "gemini-flash")
    request = _fake_request()

    first = _llm(request)
    second = _llm(request)

    assert first is second
    assert set(request.app.state.llm_clients) == {"gemini-flash"}
