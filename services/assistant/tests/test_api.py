"""Tests for the assistant FastAPI app: /healthz, /model/{scenario_slug}, the
/agui/{scenario_slug} streaming endpoint's auth/routing (not its full event
stream — that's exercised against a real LLM via Docker, see the phase-3 plan's
verification section), and the _llm_model dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from types import SimpleNamespace

import httpx
import pytest
from ag_ui.core import RunAgentInput
from ai_circus_shared.auth import Identity
from ai_circus_shared.conversations import Base as ConversationsBase
from ai_circus_shared.conversations import Conversation, ConversationStore
from ai_circus_shared.entitlements import PlatformRegistryClient
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from assistant import api as api_module
from assistant.api import (
    _chat_llm,
    _conversation_store,
    _llm_display,
    _llm_model,
    _prompt_cache,
    _scenario_definition,
    agui_endpoint,
    router,
)
from assistant.core.identity import resolve_identity
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
    session.add(Conversation(id=conversation_id, org_id=org_id, user_id=user_id, scenario_slug="churn", title="mine"))
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
            Conversation(id=conversation_id, org_id=org_id, user_id=user_id, scenario_slug="churn", title="mine")
        )
        session.commit()
    return engine


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
    app.dependency_overrides[_llm_display] = lambda: ("gpt-4o-mini", "OpenAI", True)
    app.dependency_overrides[_chat_llm] = lambda: SimpleNamespace()
    conversation_engine = _seeded_conversation_engine()
    app.dependency_overrides[_conversation_store] = lambda: ConversationStore(Session(conversation_engine))
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_healthz(client: TestClient) -> None:
    """/healthz reports ok."""
    assert client.get("/healthz").json() == {"status": "ok"}


def test_model_endpoint_returns_the_active_model_without_sending_a_message(client: TestClient) -> None:
    """GET /model/{scenario_slug} lets the UI show the model before the first chat turn."""
    response = client.get("/model/churn")

    assert response.status_code == 200
    assert response.json() == {"model": "gpt-4o-mini", "provider": "OpenAI", "vision": True}


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


def test_llm_display_resolves_the_provider_label_and_real_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """/model surfaces "model (provider)" material — the real configured model id and
    its human-readable provider label, not the bare litellm alias.
    """
    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(
        PlatformRegistryClient,
        "get_llm_provider_display",
        lambda self, *, admin_api_key, model_name: ("GroqCloud", "openai/gpt-oss-120b", False),
    )

    assert _llm_display(llm_model="groq-llama") == ("openai/gpt-oss-120b", "GroqCloud", False)


def test_llm_display_falls_back_to_the_bare_alias_when_platform_registry_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform-registry hiccup shouldn't break the /model endpoint — just drop the
    provider label and show the bare alias.
    """

    def _raise(self: PlatformRegistryClient, *, admin_api_key: str, model_name: str) -> tuple[str, str, bool] | None:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakeLlmEnvConfig())
    monkeypatch.setattr(PlatformRegistryClient, "get_llm_provider_display", _raise)

    assert _llm_display(llm_model="groq-llama") == ("groq-llama", None, False)


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
        store=_seeded_conversation_store(),
    )

    assert captured["base_url"] == "http://prediction:8000"
    assert captured["scenario_slug"] == "motor_speed"
    assert captured["authorization"] == "Bearer tok-1"
    assert captured["tools"] == ["prediction-tool-sentinel"]


async def test_agui_endpoint_turns_a_mid_run_exception_into_a_run_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider failure mid-run (e.g. GroqCloud rate-limiting an oversized request)
    must reach the client as a RUN_ERROR event, not just abort the stream — an
    abandoned stream leaves ui-react's HttpAgent waiting forever ("Thinking…" with no
    way out) instead of surfacing the error (see ChatPanel.tsx's `send()`).
    """

    async def _raising_agent_run(_input: object) -> AsyncIterator[object]:  # ruff: ignore[unused-async]
        if False:  # pragma: no cover - makes this an async generator function
            yield
        raise RuntimeError("GroqException - rate_limit_exceeded")

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakePredictionEnvConfig())
    monkeypatch.setattr(api_module, "build_prediction_tools", lambda *_a, **_kw: [])
    monkeypatch.setattr(api_module, "build_agui_agent", lambda *_a, **_kw: "fake-graph")
    monkeypatch.setattr(
        api_module, "LangGraphAGUIAgent", lambda *, name, graph: SimpleNamespace(run=_raising_agent_run)
    )

    response = await agui_endpoint(
        scenario_slug="motor_speed",
        input_data=RunAgentInput(
            threadId="t", runId="r", messages=[], tools=[], context=[], state={}, forwardedProps={}
        ),
        request=_fake_http_request("Bearer tok-1"),
        identity=Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:motor_speed"})),
        definition=SimpleNamespace(slug="motor_speed"),
        prompt_cache=SimpleNamespace(get=lambda _org_id, _slug: "system prompt"),
        llm=SimpleNamespace(),
        store=_seeded_conversation_store(),
    )

    body = "".join([chunk async for chunk in response.body_iterator])  # type: ignore[union-attr]

    assert '"type":"RUN_ERROR"' in body
    assert "rate_limit_exceeded" in body


def test_list_conversations_returns_the_fixtures_seeded_conversation(client: TestClient) -> None:
    """`client`'s persistent conversation engine is pre-seeded with one conversation
    (id="t") — the one every `/agui/...` ownership test relies on; this just confirms
    the list endpoint surfaces it.
    """
    response = client.get("/conversations/churn")

    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == ["t"]


def test_create_then_list_conversation_round_trips(client: TestClient) -> None:
    """The "+ New conversation" button's call, then the sidebar's list call."""
    created = client.post("/conversations/churn", json={"title": "My first chat"})
    assert created.status_code == 200
    assert created.json()["title"] == "My first chat"

    listed = client.get("/conversations/churn")
    ids = [c["id"] for c in listed.json()]
    assert created.json()["id"] in ids
    assert "t" in ids  # the fixture's pre-seeded conversation is still there too


def test_create_conversation_defaults_title_when_none_given(client: TestClient) -> None:
    response = client.post("/conversations/churn", json={})

    assert response.json()["title"] == "New conversation"


def test_delete_conversation_removes_it(client: TestClient) -> None:
    created = client.post("/conversations/churn", json={}).json()

    deleted = client.delete(f"/conversations/churn/{created['id']}")
    assert deleted.status_code == 200

    listed = client.get("/conversations/churn")
    assert [c["id"] for c in listed.json()] == ["t"]  # the fixture's pre-seeded conversation remains


def test_delete_unknown_conversation_returns_404(client: TestClient) -> None:
    response = client.delete("/conversations/churn/does-not-exist")

    assert response.status_code == 404


def test_list_messages_for_unknown_conversation_returns_404(client: TestClient) -> None:
    response = client.get("/conversations/churn/does-not-exist/messages")

    assert response.status_code == 404


def test_agui_endpoint_404s_for_a_thread_id_not_owned_by_this_caller(client: TestClient) -> None:
    """A guessed/stale thread id from another tenant/user must not be replayable here —
    see the agui_endpoint docstring's ownership check.
    """
    response = client.post(
        "/agui/churn",
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
        yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="Churn risk is ")
        yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="12%.")
        yield TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id="m1")

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakePredictionEnvConfig())
    monkeypatch.setattr(api_module, "build_prediction_tools", lambda *_a, **_kw: [])
    monkeypatch.setattr(api_module, "build_agui_agent", lambda *_a, **_kw: "fake-graph")
    monkeypatch.setattr(api_module, "LangGraphAGUIAgent", lambda *, name, graph: SimpleNamespace(run=_fake_agent_run))

    store = _seeded_conversation_store()
    response = await agui_endpoint(
        scenario_slug="motor_speed",
        input_data=RunAgentInput(
            threadId="t",
            runId="r",
            messages=[{"id": "u1", "role": "user", "content": "What's the churn risk?"}],
            tools=[],
            context=[],
            state={},
            forwardedProps={},
        ),
        request=_fake_http_request("Bearer tok-1"),
        identity=Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:motor_speed"})),
        definition=SimpleNamespace(slug="motor_speed"),
        prompt_cache=SimpleNamespace(get=lambda _org_id, _slug: "system prompt"),
        llm=SimpleNamespace(),
        store=store,
    )

    # Drain the stream — persistence happens in the generator's `finally` block.
    "".join([chunk async for chunk in response.body_iterator])  # type: ignore[union-attr]

    messages = store.list_messages("t", "org-1", "user-1")
    assert [(m.role, m.content) for m in messages] == [
        ("user", "What's the churn risk?"),
        ("assistant", "Churn risk is 12%."),
    ]
