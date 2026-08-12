"""
- Title:    Chat API
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from ai_circus_shared.auth import Identity
from ai_circus_shared.entitlements import PlatformRegistryClient
from ai_circus_shared.scenario_schema import ScenarioDefinition
from copilotkit import LangGraphAGUIAgent
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from assistant import get_env_config
from assistant.core.agent import build_agui_agent
from assistant.core.identity import resolve_identity
from assistant.core.prediction_client import PredictionServiceClient
from assistant.core.prompt_cache import SystemPromptCache
from assistant.core.tools import build_prediction_tools

router = APIRouter()


class ModelResponse(BaseModel):
    """Response body for GET /model/{scenario_slug}."""

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
    llm_model: str = Depends(_llm_model),
) -> ModelResponse:
    """The model that would answer this scenario's next chat message, so the UI can
    show it upfront rather than only after the first reply.
    """
    return ModelResponse(model=llm_model)


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
    system_prompt = prompt_cache.get(identity.org_id, definition.slug)
    config = get_env_config()
    prediction_client = PredictionServiceClient(base_url=config.PREDICTION_SERVICE_URL)
    tools = build_prediction_tools(
        prediction_client, scenario_slug=scenario_slug, authorization=request.headers.get("authorization")
    )
    graph = build_agui_agent(llm, system_prompt, tools)
    agent = LangGraphAGUIAgent(name=scenario_slug, graph=graph)

    encoder = EventEncoder(accept=request.headers.get("accept", ""))

    async def event_generator() -> AsyncIterator[str]:
        async for event in agent.run(input_data):
            yield encoder.encode(event)

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())
