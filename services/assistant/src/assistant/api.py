"""
- Title:    Chat API
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from ag_ui.core import RunAgentInput, RunErrorEvent
from ag_ui.encoder import EventEncoder
from ai_circus_shared.auth import Identity
from ai_circus_shared.entitlements import PlatformRegistryClient
from ai_circus_shared.scenario_schema import ScenarioDefinition
from copilotkit import LangGraphAGUIAgent
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from assistant import get_env_config
from assistant.core.agent import build_agui_agent
from assistant.core.identity import resolve_identity
from assistant.core.logger import get_logger
from assistant.core.prediction_client import PredictionServiceClient
from assistant.core.prompt_cache import SystemPromptCache
from assistant.core.tools import build_prediction_tools

router = APIRouter()
logger = get_logger(__name__)


class ModelResponse(BaseModel):
    """Response body for GET /model/{scenario_slug}."""

    model: str
    provider: str | None = None
    vision: bool = False


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


def _chat_llm(request: Request, llm_model: str = Depends(_llm_model)) -> BaseChatModel:
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


@router.post("/agui/{scenario_slug}")
async def agui_endpoint(
    scenario_slug: str,
    input_data: RunAgentInput,
    request: Request,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    prompt_cache: SystemPromptCache = Depends(_prompt_cache),
    llm: BaseChatModel = Depends(_chat_llm),
) -> StreamingResponse:
    """AG-UI (CopilotKit) streaming endpoint — same `resolve_identity`/
    `_scenario_definition` dependency chain as every other route in this service; see
    rag_agent.api.agui_endpoint for why this is hand-wired rather than using
    `ag_ui_langgraph`'s own turnkey FastAPI helper (it has no hook for this platform's
    per-request entitlement check).
    """
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    # prompt_cache.get() does blocking SeaweedFS I/O on a cache miss; this route is
    # `async def` (needed for StreamingResponse below), so FastAPI won't threadpool
    # it automatically — off the event loop explicitly, or a cold-start miss for one
    # tenant stalls every other tenant's concurrent request on this instance.
    system_prompt = await run_in_threadpool(prompt_cache.get, identity.org_id, definition.slug)
    config = get_env_config()
    prediction_client = PredictionServiceClient(base_url=config.PREDICTION_SERVICE_URL)
    tools = build_prediction_tools(
        prediction_client, scenario_slug=scenario_slug, authorization=request.headers.get("authorization")
    )
    graph = build_agui_agent(llm, system_prompt, tools)
    agent = LangGraphAGUIAgent(name=scenario_slug, graph=graph)

    encoder = EventEncoder(accept=request.headers.get("accept", ""))

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
                yield encoder.encode(event)
        except Exception as exc:
            logger.error("agui run failed for scenario={!r}: {}", scenario_slug, exc)
            yield encoder.encode(RunErrorEvent(message=str(exc)))

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())
