"""
- Title:    Chat API
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

from ai_circus_shared.auth import Identity
from fastapi import APIRouter, Depends, Request
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
    """Request body for POST /chat."""

    message: str
    history: list[ChatMessage] = []


class ChatResponse(BaseModel):
    """Response body for POST /chat."""

    reply: str


def _prompt_cache(request: Request) -> SystemPromptCache:
    return request.app.state.prompt_cache


def _llm_client(request: Request) -> OpenAI:
    return request.app.state.llm_client


def _llm_model() -> str:
    return get_env_config().LLM_MODEL


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(
    body: ChatRequest,
    identity: Identity = Depends(resolve_identity),
    prompt_cache: SystemPromptCache = Depends(_prompt_cache),
    llm_client: OpenAI = Depends(_llm_client),
    llm_model: str = Depends(_llm_model),
) -> ChatResponse:
    """Answer a question about the caller's tenant's data/model; org_id comes from their token."""
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    system_prompt = prompt_cache.get(identity.org_id)
    history = [{"role": m.role, "content": m.content} for m in body.history]
    reply = run_chat(llm_client, llm_model, system_prompt, history, body.message)
    return ChatResponse(reply=reply)
