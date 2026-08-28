"""Tests for the form-agent FastAPI app: /healthz, /model/{scenario_slug}, the
/agui/{scenario_slug} streaming endpoint's auth/routing (not its full event stream —
that's exercised against a real LLM via Docker), POST /submissions/{scenario_slug},
and the _llm/_llm_model_name dependencies.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from ag_ui.core import RunAgentInput
from ai_circus_shared.auth import Identity
from ai_circus_shared.conversations import Base as ConversationsBase
from ai_circus_shared.conversations import Conversation, ConversationStore
from ai_circus_shared.entitlements import PlatformRegistryClient
from ai_circus_shared.scenario_schema import ChatConfig, FormConfig, FormFieldSpec, VectorStoreConfig
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from form_agent import api as api_module
from form_agent.api import (
    _conversation_store,
    _embedder,
    _llm,
    _llm_display,
    _llm_model_name,
    _qdrant,
    _scenario_definition,
    _store,
    agui_endpoint,
    router,
)
from form_agent.core.identity import resolve_identity
from tests.conftest import FakeSecret


def _seeded_conversation_store(
    conversation_id: str = "t", org_id: str = "org-1", user_id: str = "user-1"
) -> ConversationStore:
    """A ConversationStore backed by a fresh in-memory SQLite database, pre-seeded
    with one conversation — stands in for `Depends(_conversation_store)` so tests
    never need a real Postgres.
    """
    engine = create_engine("sqlite:///:memory:")
    ConversationsBase.metadata.create_all(engine)
    session = Session(engine)
    session.add(
        Conversation(id=conversation_id, org_id=org_id, user_id=user_id, scenario_slug="service_request", title="mine")
    )
    session.commit()
    return ConversationStore(session)


def _seeded_conversation_engine(conversation_id: str = "t", org_id: str = "org-1", user_id: str = "user-1") -> Engine:
    """A persistent in-memory SQLite engine (one connection, kept alive via
    StaticPool) pre-seeded with one conversation — unlike `_seeded_conversation_store`
    above, this is meant to back a `_conversation_store` override reused across
    *several* TestClient requests in the same test, where a plain `sqlite:///:memory:`
    engine would otherwise hand each request its own throwaway, empty database.
    """
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    ConversationsBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Conversation(
                id=conversation_id, org_id=org_id, user_id=user_id, scenario_slug="service_request", title="mine"
            )
        )
        session.commit()
    return engine


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


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore."""

    def __init__(self) -> None:
        """Initialize an empty in-memory put log."""
        self.puts: list[tuple[str, str, bytes]] = []

    def put(self, tenant_org_id: str, path: str, data: bytes) -> str:
        """Record the put call instead of touching real SeaweedFS."""
        self.puts.append((tenant_org_id, path, data))
        return f"tenant-{tenant_org_id}/{path}"


def _fake_definition(*, classification: bool) -> SimpleNamespace:
    form = FormConfig(
        title="Public Service Request",
        fields=[
            FormFieldSpec(id="full_name", label="Full name", type="text", required=True),
            FormFieldSpec(id="email", label="Email", type="email", required=True, validation="email"),
        ]
        + (
            [FormFieldSpec(id="request_type", label="Request type", type="select", options=["a"], required=True)]
            if classification
            else []
        ),
        classification_field="request_type" if classification else None,
        classification_options=["a"] if classification else None,
    )
    return SimpleNamespace(
        slug="service_request",
        form=form,
        vector_store=VectorStoreConfig(backend="qdrant", collection_prefix="service_request", top_k=3)
        if classification
        else None,
        chat=ChatConfig(context="A generic local-government service desk."),
    )


def _client_with(
    llm: FakeToolCallingModel, *, classification: bool = False, store: FakeObjectStore | None = None
) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    fake_point = SimpleNamespace(
        payload={"text": "Routed to Public Works.", "source": "streetlight_outage.md"}, score=0.9
    )

    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:service_request"})
    )
    app.dependency_overrides[_scenario_definition] = lambda: _fake_definition(classification=classification)
    app.dependency_overrides[_qdrant] = lambda: SimpleNamespace(
        collection_exists=lambda _name: True,
        query_points=lambda **_kwargs: SimpleNamespace(points=[fake_point]),
    )
    app.dependency_overrides[_embedder] = lambda: SimpleNamespace(encode_query=lambda _q: [0.1, 0.2])
    app.dependency_overrides[_llm] = lambda: llm
    app.dependency_overrides[_llm_model_name] = lambda: "gemini-flash"
    app.dependency_overrides[_llm_display] = lambda: ("gemini-flash", "Google Gemini", True)
    app.dependency_overrides[_store] = lambda: store if store is not None else FakeObjectStore()
    conversation_engine = _seeded_conversation_engine()
    app.dependency_overrides[_conversation_store] = lambda: ConversationStore(Session(conversation_engine))
    return TestClient(app)


def test_healthz() -> None:
    """/healthz reports ok."""
    client = _client_with(FakeToolCallingModel(responses=[]))
    assert client.get("/healthz").json() == {"status": "ok"}


def test_model_endpoint_returns_the_active_model_without_sending_a_message() -> None:
    """GET /model/{scenario_slug} lets the UI show the model before the first chat turn."""
    client = _client_with(FakeToolCallingModel(responses=[]))

    response = client.get("/model/service_request")

    assert response.status_code == 200
    assert response.json() == {"model": "gemini-flash", "provider": "Google Gemini", "vision": True}


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


def test_list_conversations_returns_the_fixtures_seeded_conversation() -> None:
    """`_client_with`'s persistent conversation engine is pre-seeded with one
    conversation (id="t") — the one every `/agui/...` test below relies on for the
    ownership check to pass; this just confirms the list endpoint surfaces it.
    """
    client = _client_with(FakeToolCallingModel(responses=[]))

    response = client.get("/conversations/service_request")

    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == ["t"]


def test_create_then_list_conversation_round_trips() -> None:
    """The "+ New conversation" button's call, then the sidebar's list call."""
    client = _client_with(FakeToolCallingModel(responses=[]))

    created = client.post("/conversations/service_request", json={"title": "My first chat"})
    assert created.status_code == 200
    assert created.json()["title"] == "My first chat"

    listed = client.get("/conversations/service_request")
    ids = [c["id"] for c in listed.json()]
    assert created.json()["id"] in ids
    assert "t" in ids  # the fixture's pre-seeded conversation is still there too


def test_create_conversation_defaults_title_when_none_given() -> None:
    client = _client_with(FakeToolCallingModel(responses=[]))

    response = client.post("/conversations/service_request", json={})

    assert response.json()["title"] == "New conversation"


def test_delete_conversation_removes_it() -> None:
    client = _client_with(FakeToolCallingModel(responses=[]))
    created = client.post("/conversations/service_request", json={}).json()

    deleted = client.delete(f"/conversations/service_request/{created['id']}")
    assert deleted.status_code == 200

    listed = client.get("/conversations/service_request")
    assert [c["id"] for c in listed.json()] == ["t"]  # the fixture's pre-seeded conversation remains


def test_delete_unknown_conversation_returns_404() -> None:
    client = _client_with(FakeToolCallingModel(responses=[]))

    response = client.delete("/conversations/service_request/does-not-exist")

    assert response.status_code == 404


def test_list_messages_for_unknown_conversation_returns_404() -> None:
    client = _client_with(FakeToolCallingModel(responses=[]))

    response = client.get("/conversations/service_request/does-not-exist/messages")

    assert response.status_code == 404


def test_agui_endpoint_404s_for_a_thread_id_not_owned_by_this_caller() -> None:
    """A guessed/stale thread id from another tenant/user must not be replayable here —
    see the agui_endpoint docstring's ownership check.
    """
    client = _client_with(FakeToolCallingModel(responses=[]))

    response = client.post(
        "/agui/service_request",
        json={
            "threadId": "not-mine",
            "runId": "r",
            "messages": [],
            "tools": [],
            "context": [],
            "state": {},
            "forwardedProps": {},
        },
    )

    assert response.status_code == 404


def test_submit_persists_a_valid_submission() -> None:
    store = FakeObjectStore()
    client = _client_with(FakeToolCallingModel(responses=[]), store=store)

    response = client.post(
        "/submissions/service_request",
        json={"fields": {"full_name": "Jane Doe", "email": "jane@example.com"}},
    )

    assert response.status_code == 200
    assert "case_number" in response.json()
    assert len(store.puts) == 1


def test_submit_returns_422_with_field_errors_when_invalid() -> None:
    store = FakeObjectStore()
    client = _client_with(FakeToolCallingModel(responses=[]), store=store)

    response = client.post("/submissions/service_request", json={"fields": {"full_name": "Jane Doe"}})

    assert response.status_code == 422
    assert response.json()["detail"]["errors"] == {"email": "This field is required."}
    assert store.puts == []


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


def test_llm_model_name_uses_platform_registrys_live_active_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Settings page's live picker wins over the instance's static LLM_MODEL default."""
    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(PlatformRegistryClient, "get_active_llm_model", lambda self, *, admin_api_key: "gemini-flash")

    assert _llm_model_name() == "gemini-flash"


def test_llm_model_name_falls_back_to_static_default_when_platform_registry_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform-registry hiccup shouldn't break chat — fall back to the static LLM_MODEL."""

    def _raise(self: PlatformRegistryClient, *, admin_api_key: str) -> str:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(PlatformRegistryClient, "get_active_llm_model", _raise)

    assert _llm_model_name() == "llama3"


def test_llm_display_resolves_the_provider_label_and_vision_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """/model surfaces "model (provider)" material plus whether the model accepts
    image input — the real configured model id, its human-readable provider label,
    and vision-capability, not just the bare litellm alias.
    """
    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(
        PlatformRegistryClient,
        "get_llm_provider_display",
        lambda self, *, admin_api_key, model_name: ("OpenAI", "gpt-4o-mini", True),
    )

    assert _llm_display(model_name="gpt-4o-mini") == ("gpt-4o-mini", "OpenAI", True)


def test_llm_display_falls_back_to_the_bare_alias_when_platform_registry_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform-registry hiccup shouldn't break the /model endpoint — just drop the
    provider label/vision flag and show the bare alias.
    """

    def _raise(self: PlatformRegistryClient, *, admin_api_key: str, model_name: str) -> tuple[str, str, bool] | None:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(PlatformRegistryClient, "get_llm_provider_display", _raise)

    assert _llm_display(model_name="groq-llama") == ("groq-llama", None, False)


def test_llm_builds_a_client_for_the_given_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat model client is built against the resolved model_name."""
    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())

    llm = _llm(_fake_request(), model_name="gemini-flash")

    assert llm.model_name == "gemini-flash"


def test_llm_caches_the_client_per_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeat calls for the same active model reuse one client instead of rebuilding it."""
    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    request = _fake_request()

    first = _llm(request, model_name="gemini-flash")
    second = _llm(request, model_name="gemini-flash")

    assert first is second
    assert set(request.app.state.llm_clients) == {"gemini-flash"}


def _fake_http_request() -> Request:
    """A minimal real Request (not a mock) so `request.headers.get(...)` behaves
    exactly like it does in production, without wiring up a full ASGI call.
    """
    return Request({"type": "http", "headers": [(b"accept", b"application/json")]})


async def test_agui_endpoint_persists_the_user_message_and_assistant_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a successful run, the new user turn and the model's final text reply are
    appended to the conversation's history — independent of the per-request
    InMemorySaver, which stays unrelated to this durable history.
    """
    from ag_ui.core import EventType, TextMessageContentEvent, TextMessageEndEvent, TextMessageStartEvent

    async def _fake_agent_run(  # ruff: ignore[missing-return-type-private-function, unused-async]
        _input: object,
    ):
        yield TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id="m1", role="assistant")
        yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="Routed to ")
        yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="Public Works.")
        yield TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id="m1")

    monkeypatch.setattr(api_module, "build_agui_agent", lambda *_a, **_kw: "fake-graph")
    monkeypatch.setattr(api_module, "LangGraphAGUIAgent", lambda *, name, graph: SimpleNamespace(run=_fake_agent_run))

    store = _seeded_conversation_store()
    response = await agui_endpoint(
        scenario_slug="service_request",
        input_data=RunAgentInput(
            threadId="t",
            runId="r",
            messages=[{"id": "u1", "role": "user", "content": "Who handles a streetlight outage?"}],
            tools=[],
            context=[],
            state={},
            forwardedProps={},
        ),
        request=_fake_http_request(),
        identity=Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:service_request"})),
        definition=_fake_definition(classification=False),
        qdrant=SimpleNamespace(),
        embedder=SimpleNamespace(),
        llm=SimpleNamespace(),
        store=store,
    )

    # Drain the stream — persistence happens in the generator's `finally` block.
    "".join([chunk async for chunk in response.body_iterator])  # type: ignore[union-attr]

    messages = store.list_messages("t", "org-1", "user-1")
    assert [(m.role, m.content) for m in messages] == [
        ("user", "Who handles a streetlight outage?"),
        ("assistant", "Routed to Public Works."),
    ]
