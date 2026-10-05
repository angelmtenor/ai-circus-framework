"""
- Title:    Chat API
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from ag_ui.core import CustomEvent, EventType, RunAgentInput, RunErrorEvent
from ag_ui.encoder import EventEncoder
from ai_circus_shared.auth import Identity
from ai_circus_shared.conversations import ConversationStore, DbSession, get_session
from ai_circus_shared.embeddings import EmbeddingProvider
from ai_circus_shared.entitlements import PlatformRegistryClient
from ai_circus_shared.observability import langfuse_request_metadata
from ai_circus_shared.scenario_schema import ScenarioDefinition
from copilotkit import LangGraphAGUIAgent
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient

from assistant import get_env_config
from assistant.core.agent import ModelUsageCallback, build_agui_agent
from assistant.core.chat import documents_only_instructions
from assistant.core.extraction import MAX_OCR_CHARS, build_extraction_prompt, parse_extraction
from assistant.core.identity import resolve_identity
from assistant.core.logger import get_logger
from assistant.core.prediction_client import PredictionServiceClient
from assistant.core.prompt_cache import SystemPromptCache
from assistant.core.rubric import build_rubric_prompt, parse_rubric_response
from assistant.core.tools import build_document_tool, build_prediction_tools

router = APIRouter()
logger = get_logger(__name__)

# The chat's two scopes (ui-react sends one as AG-UI `forwardedProps.scope`): every tool,
# or — for a scenario with a `documents.tool` — that document search alone.
SCOPE_ALL = "all"
SCOPE_DOCUMENTS = "documents"
# GET /documents/{scenario_slug} bounds: a reading list, not a bulk export.
MAX_LISTED_DOCUMENTS = 80
MAX_DOCUMENT_BYTES = 64 * 1024


class ModelResponse(BaseModel):
    """Response body for GET /model/{scenario_slug}."""

    model: str
    provider: str | None = None
    vision: bool = False


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


class RubricCheckIn(BaseModel):
    """Body for POST /rubric-check/{scenario_slug}: a description of behaviour (the
    scenario's rubric_check.max_chars is enforced too).
    """

    text: str = Field(min_length=10, max_length=4000)


class RubricBehaviourOut(BaseModel):
    """One rubric behaviour the description shows, with its quoted evidence."""

    key: str
    name: str
    polarity: str  # "positive" | "negative" — from the rubric, not the model
    evidence: str
    verified: bool  # the quote really appears in the description
    strength: str
    note: str = ""


class RubricCheckOut(BaseModel):
    """The LLM's rubric reading of one description (see core/rubric.py)."""

    verdict: str  # great | mixed | toxic | unclear
    verdict_label: str
    balance: int  # -100 (negative) … 100 (positive)
    summary: str
    behaviours: list[RubricBehaviourOut]
    advice: list[str]
    model: str


class ExtractRecordIn(BaseModel):
    """Body for POST /extract-record/{scenario_slug}: the OCR text of one scanned application."""

    text: str = Field(min_length=20, max_length=MAX_OCR_CHARS)


class ExtractedFieldOut(BaseModel):
    """One box the LLM read from the scan: its value (already fitted to the box's type),
    the quote it comes from, and whether that quote really appears in the OCR text.
    """

    value: str | float
    evidence: str
    confidence: float
    verified: bool


class DocumentOut(BaseModel):
    """One reference document of a scenario's `documents.tool`, read back in full: its
    `# ` heading as title and its first `> ` line as provenance note (source, or that it
    is fictional).
    """

    name: str
    title: str
    note: str
    text: str


@dataclass(frozen=True)
class DocumentSearch:
    """What the document tool searches with: Qdrant plus the query embedder."""

    qdrant: QdrantClient
    embedder: EmbeddingProvider


class ExtractRecordOut(BaseModel):
    """The boxes read from a scan (see core/extraction.py): `rejected` holds values that
    didn't fit their box (not an option, out of range), `missing` the boxes not found.
    """

    fields: dict[str, ExtractedFieldOut]
    rejected: dict[str, str]
    missing: list[str]
    model: str


def _prompt_cache(request: Request) -> SystemPromptCache:
    return request.app.state.prompt_cache


def _llm_model() -> str:
    """The model to send to llm-gateway: live from platform-registry's admin Settings
    picker (applies on the very next chat request, no restart), falling back to this
    instance's static LLM_MODEL if platform-registry is unreachable.
    """
    config = get_env_config()
    registry = PlatformRegistryClient(base_url=config.PLATFORM_REGISTRY_URL)
    try:
        return registry.get_active_llm_model(admin_api_key=config.ADMIN_API_KEY.get_secret_value())
    except httpx.HTTPError:
        return config.LLM_MODEL


def _llm_display(llm_model: str = Depends(_llm_model)) -> tuple[str, str | None, bool]:
    """(model, provider label, vision-capable) for the UI's "model (provider)" badge —
    e.g. `("openai/gpt-oss-120b", "GroqCloud", False)`. Falls back to the bare alias
    with no provider label and vision=False if platform-registry is unreachable or
    the alias isn't routed.
    """
    config = get_env_config()
    registry = PlatformRegistryClient(base_url=config.PLATFORM_REGISTRY_URL)
    try:
        display = registry.get_llm_provider_display(
            admin_api_key=config.ADMIN_API_KEY.get_secret_value(), model_name=llm_model
        )
    except httpx.HTTPError:
        display = None
    if display is None:
        return llm_model, None, False
    label, model, vision = display
    return model, label, vision


def _chat_llm(request: Request, llm_model: str = Depends(_llm_model)) -> ChatOpenAI:
    """The LangChain chat model backing the AG-UI route — see app.py's chat_llm_clients."""
    config = get_env_config()
    clients: dict[str, ChatOpenAI] = request.app.state.chat_llm_clients
    if llm_model not in clients:
        clients[llm_model] = ChatOpenAI(
            base_url=config.LLM_GATEWAY_URL,
            api_key=config.LLM_GATEWAY_API_KEY.get_secret_value(),
            model=llm_model,
        )
    return clients[llm_model]


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


def _document_search(request: Request) -> DocumentSearch | None:
    """None when no served scenario has a document tool (app.py builds no embedder then)."""
    embedder = request.app.state.embedder
    return None if embedder is None else DocumentSearch(qdrant=request.app.state.qdrant, embedder=embedder)


def _chat_scope(input_data: RunAgentInput, definition: ScenarioDefinition) -> str:
    """The run's scope from `forwardedProps.scope` — documents-only only when asked for
    *and* the scenario has a document tool; anything else is the full toolset.
    """
    props = input_data.forwarded_props
    requested = props.get("scope") if isinstance(props, dict) else None
    has_tool = definition.documents is not None and definition.documents.tool is not None
    return SCOPE_DOCUMENTS if requested == SCOPE_DOCUMENTS and has_tool else SCOPE_ALL


def _parse_document(name: str, data: bytes) -> DocumentOut:
    text = data[:MAX_DOCUMENT_BYTES].decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines()]
    title = next((line[2:].strip() for line in lines if line.startswith("# ")), name)
    note = next((line[1:].strip() for line in lines if line.startswith(">")), "")
    return DocumentOut(name=name, title=title, note=note, text=text)


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


@router.post("/rubric-check/{scenario_slug}", response_model=RubricCheckOut)
def rubric_check_endpoint(
    body: RubricCheckIn,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    llm: ChatOpenAI = Depends(_chat_llm),
    model_name: str = Depends(_llm_model),
) -> RubricCheckOut:
    """Read a description of behaviour against the scenario's `rubric_check` rubric
    with the active LLM (plain `def`: FastAPI runs the blocking LLM call in its
    threadpool). 404 when the scenario has no rubric, 422 when the text is too long,
    502 when the model's answer isn't usable.
    """
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    rubric = definition.rubric_check
    if rubric is None:
        raise HTTPException(status_code=404, detail=f"Scenario {definition.slug!r} has no rubric check.")
    if len(body.text) > rubric.max_chars:
        raise HTTPException(status_code=422, detail=f"The description is longer than {rubric.max_chars} characters.")
    # Same per-request copy as the AG-UI route: tenant `user` for llm-gateway's budget
    # hook, Langfuse metadata for the trace, and temperature 0 for a repeatable reading.
    llm_for_request = llm.model_copy(
        update={
            "temperature": 0,
            "model_kwargs": {**llm.model_kwargs, "user": identity.org_id},
            "extra_body": {
                **(llm.extra_body or {}),
                "metadata": langfuse_request_metadata(
                    service="assistant-rubric", org_id=identity.org_id, scenario_slug=definition.slug
                ),
            },
        }
    )
    try:
        reply = llm_for_request.invoke([
            SystemMessage(content=build_rubric_prompt(rubric)),
            HumanMessage(content=f"Description to assess:\n<<<\n{body.text}\n>>>"),
        ])
    except Exception as exc:  # provider errors come in many shapes (rate limit, auth, timeout)
        logger.warning("Rubric check LLM call failed for scenario={}: {}", definition.slug, exc)
        raise HTTPException(status_code=502, detail=f"The language model could not be reached: {exc}") from exc
    content = reply.content if isinstance(reply.content, str) else str(reply.content)
    try:
        result = parse_rubric_response(content, rubric, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"The language model's answer was not usable: {exc}") from exc
    return RubricCheckOut(**result, model=model_name)


def _case_desk(definition: ScenarioDefinition) -> Any:
    """The scenario's `case_desk` extra, or a 404 — only those scenarios take scanned intake."""
    extras = definition.ui_extras
    if definition.dataset is None or extras is None or extras.kind != "case_desk":
        raise HTTPException(status_code=404, detail=f"Scenario {definition.slug!r} has no case desk intake.")
    return extras


@router.post("/extract-record/{scenario_slug}", response_model=ExtractRecordOut)
def extract_record_endpoint(
    body: ExtractRecordIn,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    llm: ChatOpenAI = Depends(_chat_llm),
    model_name: str = Depends(_llm_model),
) -> ExtractRecordOut:
    """Read a scanned application's OCR text into the scenario's boxes with the active LLM
    (plain `def`: FastAPI runs the blocking call in its threadpool). 404 when the scenario
    has no case desk, 502 when the model can't be reached or answers something unusable.
    """
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    extras = _case_desk(definition)
    assert definition.dataset is not None
    skip = {c.field for c in extras.computed_fields}
    llm_for_request = llm.model_copy(
        update={
            "temperature": 0,
            "model_kwargs": {**llm.model_kwargs, "user": identity.org_id},
            "extra_body": {
                **(llm.extra_body or {}),
                "metadata": langfuse_request_metadata(
                    service="assistant-extract", org_id=identity.org_id, scenario_slug=definition.slug
                ),
            },
        }
    )
    try:
        reply = llm_for_request.invoke([
            SystemMessage(content=build_extraction_prompt(definition.dataset, skip, extras.form_title)),
            HumanMessage(content=f"OCR text of the scanned form:\n<<<\n{body.text}\n>>>"),
        ])
    except Exception as exc:  # provider errors come in many shapes (rate limit, auth, timeout)
        logger.warning("Record extraction LLM call failed for scenario={}: {}", definition.slug, exc)
        raise HTTPException(status_code=502, detail=f"The language model could not be reached: {exc}") from exc
    content = reply.content if isinstance(reply.content, str) else str(reply.content)
    try:
        result = parse_extraction(content, definition.dataset, skip, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"The language model's answer was not usable: {exc}") from exc
    return ExtractRecordOut(**result, model=model_name)


@router.get("/intake-samples/{scenario_slug}/{filename}")
def intake_sample_endpoint(
    filename: str,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
) -> FileResponse:
    """One of the case desk's fictional scanned applications (`ui_extras.sample_uploads`) —
    only files the scenario lists are served, from its own `sample_uploads/` folder.
    """
    extras = _case_desk(definition)
    if filename not in {sample.file for sample in extras.sample_uploads}:
        raise HTTPException(status_code=404, detail="Sample document not found.")
    path = Path(get_env_config().SCENARIOS_DIR) / definition.slug / "sample_uploads" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Sample document not found.")
    return FileResponse(path, filename=filename, headers={"Cache-Control": "private, max-age=3600"})


@router.get("/documents/{scenario_slug}", response_model=list[DocumentOut])
def documents_endpoint(
    request: Request,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
) -> list[DocumentOut]:
    """The caller's tenant's reference documents for this scenario's `documents.tool` —
    the same files etl-vectorize indexed for its search, read whole for the UI's reading
    list. 404 for a scenario without a document tool.
    """
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    documents = definition.documents
    if documents is None or documents.tool is None:
        raise HTTPException(status_code=404, detail=f"Scenario {definition.slug!r} has no reference documents.")
    store = request.app.state.document_stores[definition.slug]
    keys = sorted(store.list(identity.org_id, documents.raw_prefix))[:MAX_LISTED_DOCUMENTS]
    return [_parse_document(key.removeprefix(documents.raw_prefix), store.get(identity.org_id, key)) for key in keys]


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
    prompt_cache: SystemPromptCache = Depends(_prompt_cache),
    llm: ChatOpenAI = Depends(_chat_llm),
    model_name: str = Depends(_llm_model),
    store: ConversationStore = Depends(_conversation_store),
    document_search: DocumentSearch | None = Depends(_document_search),
) -> StreamingResponse:
    """AG-UI (CopilotKit) streaming endpoint — same `resolve_identity`/
    `_scenario_definition` dependency chain as every other route in this service; see
    rag_agent.api.agui_endpoint for why this is hand-wired rather than using
    `ag_ui_langgraph`'s own turnkey FastAPI helper (it has no hook for this platform's
    per-request entitlement check).
    """
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    # Every DB call in this `async def` route goes through the threadpool: a sync call
    # here would block the event loop that is also streaming every other chat.
    if await run_in_threadpool(store.get_conversation, input_data.thread_id, identity.org_id, identity.subject) is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    # prompt_cache.get() does blocking SeaweedFS I/O on a cache miss; this route is
    # `async def` (needed for StreamingResponse below), so FastAPI won't threadpool
    # it automatically — off the event loop explicitly, or a cold-start miss for one
    # tenant stalls every other tenant's concurrent request on this instance.
    system_prompt = await run_in_threadpool(prompt_cache.get, identity.org_id, definition.slug)
    config = get_env_config()
    # Documents-only mode drops the dataset/prediction tools from the run itself (not
    # just from the prompt), so the model cannot reach the data or the model there.
    scope = _chat_scope(input_data, definition)
    tools: list[BaseTool] = []
    if scope == SCOPE_ALL:
        prediction_client = PredictionServiceClient(base_url=config.PREDICTION_SERVICE_URL)
        tools = build_prediction_tools(
            prediction_client, scenario_slug=scenario_slug, authorization=request.headers.get("authorization")
        )
    else:
        system_prompt += documents_only_instructions(definition)
    if definition.documents is not None and definition.documents.tool is not None and document_search is not None:
        tools.append(
            build_document_tool(document_search.qdrant, document_search.embedder, definition, org_id=identity.org_id)
        )
    # model_copy(): a new ChatOpenAI wrapping the shared, model-name-keyed cached
    # client (see _chat_llm) without mutating it — per-request only, so concurrent
    # calls from other orgs through the same cached client never see this one's
    # user. `user` is a first-class OpenAI Chat Completions field (merged in via
    # model_kwargs, langchain_openai's documented mechanism for exactly this), so
    # it reaches llm-gateway's request body untouched — see llm_gateway.budget_hook's
    # module docstring for the per-tenant budget enforcement this makes possible.
    # `extra_body.metadata` rides along the same way and is what llm-gateway's Langfuse
    # callback turns into the trace's tenant/session/scenario tags — see
    # ai_circus_shared.observability.langfuse_request_metadata.
    llm_for_request = llm.model_copy(
        update={
            "model_kwargs": {**llm.model_kwargs, "user": identity.org_id},
            "extra_body": {
                **(llm.extra_body or {}),
                "metadata": langfuse_request_metadata(
                    service="assistant",
                    org_id=identity.org_id,
                    scenario_slug=scenario_slug,
                    thread_id=input_data.thread_id,
                ),
            },
        }
    )
    graph = build_agui_agent(llm_for_request, system_prompt, tools)
    model_usage = ModelUsageCallback()
    agent = LangGraphAGUIAgent(name=scenario_slug, graph=graph, config={"callbacks": [model_usage]})

    encoder = EventEncoder(accept=request.headers.get("accept", ""))
    assistant_text_by_message_id: dict[str, list[str]] = {}
    fallback_reported_message_ids: set[str] = set()

    async def event_generator() -> AsyncIterator[str]:
        # Left uncaught, a mid-run exception (e.g. the LLM provider rate-limiting or
        # rejecting an oversized request — routine on GroqCloud's free tier once a
        # tool result makes the prompt large) aborts this generator, which just closes
        # the connection with no terminal AG-UI event. ui-react's HttpAgent then waits
        # forever for one, showing "Thinking…" indefinitely instead of an error — see
        # ChatPanel.tsx's `catch` in `send()`, which already renders a `RunErrorEvent`
        # as a chat bubble once it actually gets one.
        try:
            async for event in agent.run(input_data):
                if event.type == EventType.TEXT_MESSAGE_CONTENT:
                    assistant_text_by_message_id.setdefault(event.message_id, []).append(event.delta)
                elif (
                    event.type == EventType.TEXT_MESSAGE_END
                    and event.message_id not in fallback_reported_message_ids
                    and "".join(assistant_text_by_message_id.get(event.message_id, []))
                    and model_usage.served_model
                    and model_usage.served_model != model_name
                ):
                    # Emitted before RUN_FINISHED (not after — see rag_agent.api's
                    # sibling implementation), so ChatPanel.tsx's onCustomEvent
                    # subscriber sees it within the same run's live event stream.
                    fallback_reported_message_ids.add(event.message_id)
                    yield encoder.encode(
                        CustomEvent(
                            name="model_fallback",
                            value={
                                "message_id": event.message_id,
                                "requested_model": model_name,
                                "served_model": model_usage.served_model,
                            },
                        )
                    )
                yield encoder.encode(event)
        except Exception as exc:
            logger.error("agui run failed for scenario={!r}: {}", scenario_slug, exc)
            yield encoder.encode(RunErrorEvent(message=str(exc)))
        finally:
            await run_in_threadpool(_persist_turn, store, input_data, identity, assistant_text_by_message_id)

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())
