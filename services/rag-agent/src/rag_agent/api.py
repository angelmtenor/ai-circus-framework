"""
- Title:    RAG chat API
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

from ai_circus_shared.auth import Identity
from ai_circus_shared.scenario_schema import ScenarioDefinition
from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from rag_agent.core.agent import run_chat
from rag_agent.core.identity import resolve_identity

router = APIRouter()


class ChatMessage(BaseModel):
    """One turn of prior conversation history."""

    role: str
    content: str


class ChatRequest(BaseModel):
    """Request body for POST /chat/{scenario_slug}."""

    message: str
    history: list[ChatMessage] = []


class SourceOut(BaseModel):
    """One retrieved chunk's source and similarity score, returned for transparency."""

    source: str
    score: float


class ChatResponse(BaseModel):
    """Response body for POST /chat/{scenario_slug}. `sources` is empty if the agent
    judged the question off-topic and answered without calling the retrieval tool.
    """

    reply: str
    sources: list[SourceOut]


def _qdrant(request: Request) -> QdrantClient:
    return request.app.state.qdrant


def _embedders(request: Request) -> dict[str, SentenceTransformer]:
    return request.app.state.embedders


def _llm(request: Request) -> BaseChatModel:
    return request.app.state.llm


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
    qdrant: QdrantClient = Depends(_qdrant),
    embedders: dict[str, SentenceTransformer] = Depends(_embedders),
    llm: BaseChatModel = Depends(_llm),
) -> ChatResponse:
    """Answer a question, retrieving from the caller's tenant's vectorized documents only if in-domain."""
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    assert definition.vector_store is not None  # guaranteed by kind="conversational_rag" filter
    embedder = embedders[definition.slug]
    history = [{"role": m.role, "content": m.content} for m in body.history]
    reply, sources = run_chat(
        llm, qdrant, embedder, definition.vector_store, identity.org_id, definition.chat.context, history, body.message
    )
    return ChatResponse(reply=reply, sources=[SourceOut(**s) for s in sources])
