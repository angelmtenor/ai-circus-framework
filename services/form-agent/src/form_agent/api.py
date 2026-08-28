"""
- Title:    Form-filling chat + submission API
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
from ag_ui.core import EventType, RunAgentInput
from ag_ui.encoder import EventEncoder
from ai_circus_shared.auth import Identity
from ai_circus_shared.conversations import ConversationStore, DbSession, get_session
from ai_circus_shared.embeddings import EmbeddingProvider
from ai_circus_shared.entitlements import PlatformRegistryClient
from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore
from copilotkit import LangGraphAGUIAgent
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from qdrant_client import QdrantClient

from form_agent import get_env_config
from form_agent.core.agent import build_agui_agent, build_catalog_retrieve_tool
from form_agent.core.identity import resolve_identity
from form_agent.core.logger import get_logger
from form_agent.core.prompt import build_form_system_prompt
from form_agent.core.submissions import submit

router = APIRouter()
logger = get_logger(__name__)


class ModelResponse(BaseModel):
    """Response body for GET /model/{scenario_slug}."""

    model: str
    provider: str | None = None
    vision: bool = False


class SubmissionIn(BaseModel):
    """Body for POST /submissions/{scenario_slug} — one value per form field id."""

    fields: dict[str, str]


class SubmissionOut(BaseModel):
    """Response body for a successfully persisted submission."""

    case_number: str


class ConversationOut(BaseModel):
    """One conversation as listed/created for the UI's conversation sidebar."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationCreateIn(BaseModel):
    """Body for POST /conversations/{scenario_slug} — title defaults to "New conversation"."""

    title: str | None = None


class MessageOut(BaseModel):
    """One persisted chat message, replayed into the frontend transcript on resume."""

    id: str
    role: str
    content: Any
    created_at: datetime


def _qdrant(request: Request) -> QdrantClient:
    return request.app.state.qdrant


def _embedder(request: Request) -> EmbeddingProvider:
    return request.app.state.embedder


def _store(request: Request) -> ObjectStore:
    return request.app.state.store


def _llm_model_name() -> str:
    """The model name to send to llm-gateway: live from platform-registry's admin
    Settings picker (applies on the very next chat request, no restart), falling back
    to this instance's static LLM_MODEL if platform-registry is unreachable.
    """
    config = get_env_config()
    registry = PlatformRegistryClient(base_url=config.PLATFORM_REGISTRY_URL)
    try:
        return registry.get_active_llm_model(admin_api_key=config.ADMIN_API_KEY.get_secret_value())
    except httpx.HTTPError:
        return config.LLM_MODEL


def _llm_display(model_name: str = Depends(_llm_model_name)) -> tuple[str, str | None, bool]:
    """(model, provider label, vision-capable) for the UI's "model (provider)" badge —
    e.g. `("openai/gpt-oss-120b", "GroqCloud", False)`. Falls back to the bare alias
    with no provider label and vision=False if platform-registry is unreachable or
    the alias isn't routed.
    """
    config = get_env_config()
    registry = PlatformRegistryClient(base_url=config.PLATFORM_REGISTRY_URL)
    try:
        display = registry.get_llm_provider_display(
            admin_api_key=config.ADMIN_API_KEY.get_secret_value(), model_name=model_name
        )
    except httpx.HTTPError:
        display = None
    if display is None:
        return model_name, None, False
    label, model, vision = display
    return model, label, vision


def _llm(request: Request, model_name: str = Depends(_llm_model_name)) -> BaseChatModel:
    """The chat model to use, cached per model_name on app.state so repeat requests
    for the same model reuse one client.
    """
    config = get_env_config()
    llm_clients: dict[str, ChatOpenAI] = request.app.state.llm_clients
    if model_name not in llm_clients:
        llm_clients[model_name] = ChatOpenAI(
            base_url=config.LLM_GATEWAY_URL,
            api_key=config.LLM_GATEWAY_API_KEY.get_secret_value(),
            model=model_name,
        )
    return llm_clients[model_name]


def _conversation_store(session: DbSession = Depends(get_session)) -> ConversationStore:
    return ConversationStore(session)


def _scenario_definition(scenario_slug: str, request: Request) -> ScenarioDefinition:
    """Look up `scenario_slug` among the scenarios this instance loaded at startup.

    A scenario can be a real, entitled scenario in platform-registry yet still 404
    here if this specific instance's SCENARIOS env var doesn't include it — that's a
    "not served here" condition, distinct from (and checked after) the 401/403s
    `resolve_identity` raises for auth/entitlement failures.
    """
    definitions: dict[str, ScenarioDefinition] = request.app.state.definitions
    definition = definitions.get(scenario_slug)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_slug!r} is not served by this instance.")
    return definition


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


@router.get("/model/{scenario_slug}", response_model=ModelResponse)
def model_endpoint(
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    display: tuple[str, str | None, bool] = Depends(_llm_display),
) -> ModelResponse:
    """The model (and its provider) that would answer this scenario's next chat
    message, so the UI can show it upfront rather than only after the first reply.
    `vision` tells ui-react's ChatPanel whether an attached image can go straight to
    this model or needs OCR text-extraction first.
    """
    model, provider, vision = display
    return ModelResponse(model=model, provider=provider, vision=vision)


@router.get("/conversations/{scenario_slug}", response_model=list[ConversationOut])
def list_conversations_endpoint(
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    store: ConversationStore = Depends(_conversation_store),
) -> list[ConversationOut]:
    """List this caller's past conversations for this scenario, most-recently-updated first."""
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    conversations = store.list_conversations(identity.org_id, identity.subject, definition.slug)
    return [
        ConversationOut(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at) for c in conversations
    ]


@router.post("/conversations/{scenario_slug}", response_model=ConversationOut)
def create_conversation_endpoint(
    body: ConversationCreateIn,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    store: ConversationStore = Depends(_conversation_store),
) -> ConversationOut:
    """Start a new, empty conversation — the UI's "+ New conversation" button."""
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    conversation = store.create_conversation(
        identity.org_id, identity.subject, definition.slug, body.title or "New conversation"
    )
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("/conversations/{scenario_slug}/{conversation_id}/messages", response_model=list[MessageOut])
def list_conversation_messages_endpoint(
    conversation_id: str,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    store: ConversationStore = Depends(_conversation_store),
) -> list[MessageOut]:
    """Full transcript for resuming a past conversation — 404 if it's unknown or not this caller's."""
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    if store.get_conversation(conversation_id, identity.org_id, identity.subject) is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    messages = store.list_messages(conversation_id, identity.org_id, identity.subject)
    return [MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at) for m in messages]


@router.delete("/conversations/{scenario_slug}/{conversation_id}")
def delete_conversation_endpoint(
    conversation_id: str,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    store: ConversationStore = Depends(_conversation_store),
) -> dict[str, bool]:
    """Delete a conversation and its messages — 404 if it's unknown or not this caller's."""
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    if not store.delete_conversation(conversation_id, identity.org_id, identity.subject):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"deleted": True}


def _persist_turn(
    store: ConversationStore,
    input_data: RunAgentInput,
    identity: Identity,
    assistant_text_by_message_id: dict[str, list[str]],
) -> None:
    """Append this turn's new user message and the model's final text reply (if any)
    to the conversation's history. Best-effort: a persistence hiccup here must never
    surface as a chat error — the client has already received (or errored on) the
    real response by the time this runs.
    """
    assert identity.org_id is not None
    turns: list[tuple[str, Any]] = []
    last = input_data.messages[-1] if input_data.messages else None
    if last is not None and last.role == "user":
        content = last.content if isinstance(last.content, str) else [c.model_dump(mode="json") for c in last.content]
        turns.append(("user", content))
    for deltas in assistant_text_by_message_id.values():
        text = "".join(deltas)
        if text:
            turns.append(("assistant", text))
    if not turns:
        return
    try:
        store.append_messages(input_data.thread_id, identity.org_id, identity.subject, turns)
    except Exception:
        logger.error("Failed to persist conversation history for thread_id={!r}", input_data.thread_id)


@router.post("/agui/{scenario_slug}")
async def agui_endpoint(
    scenario_slug: str,
    input_data: RunAgentInput,
    request: Request,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    qdrant: QdrantClient = Depends(_qdrant),
    embedder: EmbeddingProvider = Depends(_embedder),
    llm: BaseChatModel = Depends(_llm),
    store: ConversationStore = Depends(_conversation_store),
) -> StreamingResponse:
    """AG-UI (CopilotKit) streaming endpoint — same `resolve_identity`/
    `_scenario_definition` dependency chain, and the same reason for hand-wiring
    instead of `ag_ui_langgraph`'s turnkey router, as rag_agent.api.agui_endpoint.

    `retrieve_catalog` is only given to the agent when the scenario configures
    classification (`form.classification_field`) — a plain slot-filling scenario runs
    with no server-side tools at all; `update_form_fields` always arrives from the
    frontend's own tool declarations in `input_data.tools` (see core/agent.py).

    `input_data.thread_id` is a real conversation id — created via
    `POST /conversations/{scenario_slug}` above, never the bare scenario slug — and
    must already belong to this org/user, so a guessed/stale thread id from another
    tenant can't be replayed here. Once the stream completes, this turn's new user
    message and the model's final text reply are appended to that conversation's
    history (see ai_circus_shared.conversations) — independent of the per-request
    `InMemorySaver` in build_agui_agent, which stays unrelated to this durable history.
    """
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    assert definition.form is not None  # guaranteed by kind="assisted_form" filter
    if store.get_conversation(input_data.thread_id, identity.org_id, identity.subject) is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    tools: list[BaseTool] = []
    if definition.form.classification_field is not None:
        assert definition.vector_store is not None  # enforced by scenario_schema's own validator
        tools.append(build_catalog_retrieve_tool(qdrant, embedder, definition.vector_store, identity.org_id))

    system_prompt = build_form_system_prompt(definition)
    graph = build_agui_agent(llm, system_prompt, tools)
    agent = LangGraphAGUIAgent(name=scenario_slug, graph=graph)

    encoder = EventEncoder(accept=request.headers.get("accept", ""))
    assistant_text_by_message_id: dict[str, list[str]] = {}

    async def event_generator() -> AsyncIterator[str]:
        # Unlike rag_agent's agui_endpoint, no try/except wraps this loop — form-agent
        # currently lets a mid-run exception abort the generator uncaught (a pre-existing
        # difference between the services, out of scope to change here). The bare
        # try/finally below only ensures persistence still runs on the way out, whether
        # the loop finishes normally or raises.
        try:
            async for event in agent.run(input_data):
                if event.type == EventType.TEXT_MESSAGE_CONTENT:
                    assistant_text_by_message_id.setdefault(event.message_id, []).append(event.delta)
                yield encoder.encode(event)
        finally:
            _persist_turn(store, input_data, identity, assistant_text_by_message_id)

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())


@router.post("/submissions/{scenario_slug}")
def submit_endpoint(
    scenario_slug: str,
    body: SubmissionIn,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    store: ObjectStore = Depends(_store),
) -> SubmissionOut:
    """Validate and persist a form submission.

    422 with `{"errors": {field_id: message}}` on validation failure — the backend is
    the final authority (see ai_circus_shared.form_validation); ui-react's own
    client-side validation is only for instant feedback before this call.
    """
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    case_number, errors = submit(store, identity.org_id, definition, body.fields)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    assert case_number is not None
    return SubmissionOut(case_number=case_number)
