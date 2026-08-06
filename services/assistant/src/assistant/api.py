"""
- Title:    Chat API
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

import httpx
from ai_circus_shared.auth import Identity
from ai_circus_shared.entitlements import PlatformRegistryClient
from ai_circus_shared.scenario_schema import ScenarioDefinition
from fastapi import APIRouter, Depends, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel

from assistant import get_env_config
from assistant.core.chat import chat as run_chat
from assistant.core.identity import resolve_identity
from assistant.core.prompt_cache import SystemPromptCache

router = APIRouter()


class ChatMessage(BaseModel):
    """One turn of prior conversation history."""

    role: str
    content: str


class ChatRequest(BaseModel):
    """Request body for POST /chat/{scenario_slug}."""

    message: str
    history: list[ChatMessage] = []


class ChatResponse(BaseModel):
    """Response body for POST /chat/{scenario_slug}."""

    reply: str


def _prompt_cache(request: Request) -> SystemPromptCache:
    return request.app.state.prompt_cache


def _llm_client(request: Request) -> OpenAI:
    return request.app.state.llm_client


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


@router.post("/chat/{scenario_slug}", response_model=ChatResponse)
def chat_endpoint(
    body: ChatRequest,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    prompt_cache: SystemPromptCache = Depends(_prompt_cache),
    llm_client: OpenAI = Depends(_llm_client),
    llm_model: str = Depends(_llm_model),
) -> ChatResponse:
    """Answer a question about the caller's tenant's data/model; org_id comes from their token."""
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    system_prompt = prompt_cache.get(identity.org_id, definition.slug)
    history = [{"role": m.role, "content": m.content} for m in body.history]
    reply = run_chat(llm_client, llm_model, system_prompt, history, body.message)
    return ChatResponse(reply=reply)
