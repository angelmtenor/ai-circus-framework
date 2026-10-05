"""Tests for the assistant FastAPI app: /healthz, /model/{scenario_slug}, the
/agui/{scenario_slug} streaming endpoint's auth/routing (not its full event
stream — that's exercised against a real LLM via Docker, see the phase-3 plan's
verification section), and the _llm_model dependency.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Generator
from types import SimpleNamespace
from typing import Any, ClassVar

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
    _document_search,
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
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
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
    app.dependency_overrides[_document_search] = lambda: None
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
        api_module,
        "LangGraphAGUIAgent",
        lambda *, name, graph, config=None: SimpleNamespace(run=lambda _input: iter(())),
    )

    await agui_endpoint(
        scenario_slug="motor_speed",
        input_data=RunAgentInput(
            threadId="t", runId="r", messages=[], tools=[], context=[], state={}, forwardedProps={}
        ),
        request=_fake_http_request("Bearer tok-1"),
        identity=Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:motor_speed"})),
        definition=SimpleNamespace(slug="motor_speed", documents=None),
        prompt_cache=SimpleNamespace(get=lambda _org_id, _slug: "system prompt"),
        llm=SimpleNamespace(model_kwargs={}, extra_body=None, model_copy=lambda **_kw: SimpleNamespace()),
        model_name="gemini-flash",
        store=_seeded_conversation_store(),
        document_search=None,
    )

    assert captured["base_url"] == "http://prediction:8000"
    assert captured["scenario_slug"] == "motor_speed"
    assert captured["authorization"] == "Bearer tok-1"
    assert captured["tools"] == ["prediction-tool-sentinel"]


async def test_agui_endpoint_binds_the_callers_org_id_onto_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-tenant AI Gateway budgets (llm_gateway.budget_hook) are enforced by the
    org_id carried in each request's `user` field — set via model_copy() per
    request, never baked into the shared model-name-keyed cached client (see
    _chat_llm), so concurrent calls from other orgs never see this one's user.
    """
    captured_model_kwargs: dict[str, object] = {}
    captured_extra_body: dict[str, object] = {}

    class FakeLlm:
        model_kwargs: ClassVar[dict[str, object]] = {}
        extra_body: ClassVar[dict[str, object] | None] = None

        def model_copy(self, *, update: dict[str, object]) -> FakeLlm:
            captured_model_kwargs.update(update["model_kwargs"])
            captured_extra_body.update(update["extra_body"])
            return self

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakePredictionEnvConfig())
    monkeypatch.setattr(api_module, "build_prediction_tools", lambda *_a, **_kw: [])
    monkeypatch.setattr(api_module, "build_agui_agent", lambda *_a, **_kw: "fake-graph")
    monkeypatch.setattr(
        api_module,
        "LangGraphAGUIAgent",
        lambda *, name, graph, config=None: SimpleNamespace(run=lambda _input: iter(())),
    )

    await agui_endpoint(
        scenario_slug="motor_speed",
        input_data=RunAgentInput(
            threadId="t", runId="r", messages=[], tools=[], context=[], state={}, forwardedProps={}
        ),
        request=_fake_http_request("Bearer tok-1"),
        identity=Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:motor_speed"})),
        definition=SimpleNamespace(slug="motor_speed", documents=None),
        prompt_cache=SimpleNamespace(get=lambda _org_id, _slug: "system prompt"),
        llm=FakeLlm(),
        model_name="gemini-flash",
        store=_seeded_conversation_store(),
        document_search=None,
    )

    assert captured_model_kwargs == {"user": "org-1"}
    # The same per-request copy carries the Langfuse trace tags llm-gateway forwards —
    # tenant, scenario and the conversation thread as the Langfuse session.
    metadata = captured_extra_body["metadata"]
    assert metadata["trace_user_id"] == "org-1"
    assert metadata["session_id"] == "t"
    assert any(tag.startswith("scenario:") for tag in metadata["tags"])


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
        api_module, "LangGraphAGUIAgent", lambda *, name, graph, config=None: SimpleNamespace(run=_raising_agent_run)
    )

    response = await agui_endpoint(
        scenario_slug="motor_speed",
        input_data=RunAgentInput(
            threadId="t", runId="r", messages=[], tools=[], context=[], state={}, forwardedProps={}
        ),
        request=_fake_http_request("Bearer tok-1"),
        identity=Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:motor_speed"})),
        definition=SimpleNamespace(slug="motor_speed", documents=None),
        prompt_cache=SimpleNamespace(get=lambda _org_id, _slug: "system prompt"),
        llm=SimpleNamespace(model_kwargs={}, extra_body=None, model_copy=lambda **_kw: SimpleNamespace()),
        model_name="gemini-flash",
        store=_seeded_conversation_store(),
        document_search=None,
    )

    body = "".join([chunk async for chunk in response.body_iterator])  # type: ignore[union-attr]

    assert '"type":"RUN_ERROR"' in body
    assert "rate_limit_exceeded" in body


async def test_agui_endpoint_emits_model_fallback_event_when_served_model_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When litellm_config.yaml's `litellm_settings.fallbacks` fires, the model that
    actually answered differs from the one requested — this must reach the client as
    a CUSTOM `model_fallback` event (see ChatPanel.tsx's `onCustomEvent`), not a
    silent swap the user is never told about.
    """
    from ag_ui.core import EventType, TextMessageContentEvent, TextMessageEndEvent, TextMessageStartEvent

    async def _fake_agent_run(_input: object):  # ruff: ignore[missing-return-type-private-function, unused-async]
        yield TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id="m1", role="assistant")
        yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="Churn risk is 12%.")
        yield TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id="m1")

    def _fake_langgraph_agui_agent(*, name: str, graph: object, config: dict | None = None) -> SimpleNamespace:
        # Simulates the underlying LLM call's on_llm_end having already fired by the
        # time this message streams out — see ModelUsageCallback's docstring.
        assert config is not None
        config["callbacks"][0].served_model = "groq-llama"
        return SimpleNamespace(run=_fake_agent_run)

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakePredictionEnvConfig())
    monkeypatch.setattr(api_module, "build_prediction_tools", lambda *_a, **_kw: [])
    monkeypatch.setattr(api_module, "build_agui_agent", lambda *_a, **_kw: "fake-graph")
    monkeypatch.setattr(api_module, "LangGraphAGUIAgent", _fake_langgraph_agui_agent)

    response = await agui_endpoint(
        scenario_slug="motor_speed",
        input_data=RunAgentInput(
            threadId="t", runId="r", messages=[], tools=[], context=[], state={}, forwardedProps={}
        ),
        request=_fake_http_request("Bearer tok-1"),
        identity=Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:motor_speed"})),
        definition=SimpleNamespace(slug="motor_speed", documents=None),
        prompt_cache=SimpleNamespace(get=lambda _org_id, _slug: "system prompt"),
        llm=SimpleNamespace(model_kwargs={}, extra_body=None, model_copy=lambda **_kw: SimpleNamespace()),
        model_name="gemini-flash",
        store=_seeded_conversation_store(),
        document_search=None,
    )

    body = "".join([chunk async for chunk in response.body_iterator])  # type: ignore[union-attr]

    assert '"name":"model_fallback"' in body
    assert '"requested_model":"gemini-flash"' in body
    assert '"served_model":"groq-llama"' in body
    # Emitted within the live stream, not after the run has already finished.
    assert body.index('"name":"model_fallback"') < body.index('"type":"TEXT_MESSAGE_END"')


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
    monkeypatch.setattr(
        api_module, "LangGraphAGUIAgent", lambda *, name, graph, config=None: SimpleNamespace(run=_fake_agent_run)
    )

    store = _seeded_conversation_store()
    # The route is `async def`: every blocking DB call must run in the threadpool, never
    # on the event loop that is streaming every other chat on this instance.
    db_calls_on_event_loop: list[str] = []

    def _off_loop(method: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                asyncio.get_running_loop()
                db_calls_on_event_loop.append(method.__name__)
            except RuntimeError:
                pass
            return method(*args, **kwargs)

        return wrapper

    store.get_conversation = _off_loop(store.get_conversation)  # type: ignore[method-assign]
    store.append_messages = _off_loop(store.append_messages)  # type: ignore[method-assign]
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
        definition=SimpleNamespace(slug="motor_speed", documents=None),
        prompt_cache=SimpleNamespace(get=lambda _org_id, _slug: "system prompt"),
        llm=SimpleNamespace(model_kwargs={}, extra_body=None, model_copy=lambda **_kw: SimpleNamespace()),
        model_name="gemini-flash",
        store=store,
        document_search=None,
    )

    # Drain the stream — persistence happens in the generator's `finally` block.
    "".join([chunk async for chunk in response.body_iterator])  # type: ignore[union-attr]
    assert db_calls_on_event_loop == []  # checked before this test's own store reads below

    messages = store.list_messages("t", "org-1", "user-1")
    assert [(m.role, m.content) for m in messages] == [
        ("user", "What's the churn risk?"),
        ("assistant", "Churn risk is 12%."),
    ]


# --- documents.tool: chat scope + the reading list ---


async def _run_agui_with_documents(monkeypatch: pytest.MonkeyPatch, forwarded_props: dict[str, Any]) -> dict[str, Any]:
    """Run agui_endpoint for a scenario with a document tool; return the tools and prompt the agent got."""
    from tests.test_core_chat import DEFINITION_WITH_DOCUMENTS

    captured: dict[str, Any] = {}

    def fake_build_agui_agent(llm: object, system_prompt: str, tools: list[Any]) -> str:
        captured["tools"], captured["system_prompt"] = tools, system_prompt
        return "fake-graph"

    monkeypatch.setattr(api_module, "get_env_config", lambda: _FakePredictionEnvConfig())
    monkeypatch.setattr(api_module, "build_prediction_tools", lambda *_a, **_kw: ["prediction-tool"])
    monkeypatch.setattr(api_module, "build_document_tool", lambda *_a, **kw: f"document-tool:{kw['org_id']}")
    monkeypatch.setattr(api_module, "build_agui_agent", fake_build_agui_agent)
    monkeypatch.setattr(
        api_module,
        "LangGraphAGUIAgent",
        lambda *, name, graph, config=None: SimpleNamespace(run=lambda _input: iter(())),
    )
    await agui_endpoint(
        scenario_slug="churn",
        input_data=RunAgentInput(
            threadId="t", runId="r", messages=[], tools=[], context=[], state={}, forwardedProps=forwarded_props
        ),
        request=_fake_http_request("Bearer tok-1"),
        identity=Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})),
        definition=DEFINITION_WITH_DOCUMENTS,
        prompt_cache=SimpleNamespace(get=lambda _org_id, _slug: "system prompt"),
        llm=SimpleNamespace(model_kwargs={}, extra_body=None, model_copy=lambda **_kw: SimpleNamespace()),
        model_name="gemini-flash",
        store=_seeded_conversation_store(),
        document_search=api_module.DocumentSearch(qdrant=SimpleNamespace(), embedder=SimpleNamespace()),  # type: ignore[arg-type]
    )
    return captured


async def test_agui_full_scope_adds_the_document_tool_to_the_prediction_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a scope (or an unknown one) the agent gets every tool, the document search bound to the caller's org."""
    for props in ({}, {"scope": "everything"}):
        captured = await _run_agui_with_documents(monkeypatch, props)
        assert captured["tools"] == ["prediction-tool", "document-tool:org-1"]
        assert captured["system_prompt"] == "system prompt"


async def test_agui_documents_scope_gives_the_document_tool_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documents-only mode removes the dataset/prediction tools from the run itself, not just from the prompt."""
    captured = await _run_agui_with_documents(monkeypatch, {"scope": "documents"})

    assert captured["tools"] == ["document-tool:org-1"]
    assert "DOCUMENTS-ONLY MODE" in captured["system_prompt"]


def test_chat_scope_ignores_documents_mode_for_a_scenario_without_a_document_tool() -> None:
    from tests.test_core_chat import DEFINITION

    input_data = RunAgentInput(
        threadId="t", runId="r", messages=[], tools=[], context=[], state={}, forwardedProps={"scope": "documents"}
    )
    assert api_module._chat_scope(input_data, DEFINITION) == "all"


class _FakeDocumentStore:
    """ObjectStore stand-in holding one tenant's documents."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.orgs: list[str] = []

    def list(self, tenant_org_id: str, prefix: str = "") -> list[str]:
        self.orgs.append(tenant_org_id)
        return [key for key in self.files if key.startswith(prefix)]

    def get(self, tenant_org_id: str, path: str) -> bytes:
        return self.files[path]


def _documents_client(definition: Any, store: _FakeDocumentStore) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.document_stores = {"churn": store}
    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})
    )
    app.dependency_overrides[_scenario_definition] = lambda: definition
    return TestClient(app)


def test_documents_endpoint_reads_the_callers_tenant_documents_with_title_and_note() -> None:
    from tests.test_core_chat import DEFINITION_WITH_DOCUMENTS

    store = _FakeDocumentStore({
        "normativa/ley39_art68.md": "# Ley 39/2015 · Artículo 68\n> Fuente: BOE-A-2015-10565\n\n1. Diez días.".encode(),
        "normativa/sin_titulo.md": b"Texto sin cabecera.",
        "otra/carpeta.md": b"# No listado",
    })

    response = _documents_client(DEFINITION_WITH_DOCUMENTS, store).get("/documents/churn")

    assert response.status_code == 200
    body = response.json()
    assert [d["name"] for d in body] == ["ley39_art68.md", "sin_titulo.md"]
    assert body[0]["title"] == "Ley 39/2015 · Artículo 68"
    assert body[0]["note"] == "Fuente: BOE-A-2015-10565"
    assert body[1]["title"] == "sin_titulo.md" and body[1]["note"] == ""
    assert store.orgs == ["org-1"]


def test_documents_endpoint_404s_for_a_scenario_without_a_document_tool() -> None:
    from tests.test_core_chat import DEFINITION

    response = _documents_client(DEFINITION, _FakeDocumentStore({})).get("/documents/churn")

    assert response.status_code == 404
