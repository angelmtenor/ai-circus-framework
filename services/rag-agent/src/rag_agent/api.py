"""
- Title:    RAG chat API
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

from ai_circus_shared.auth import Identity
from ai_circus_shared.scenario_schema import VectorStoreConfig
from fastapi import APIRouter, Depends, Request
from openai import OpenAI
from pydantic import BaseModel
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from rag_agent import get_env_config
from rag_agent.core.identity import resolve_identity
from rag_agent.core.rag_chat import chat as run_chat
from rag_agent.core.retrieval import retrieve

router = APIRouter()


class ChatMessage(BaseModel):
    """One turn of prior conversation history."""

    role: str
    content: str


class ChatRequest(BaseModel):
    """Request body for POST /chat."""

    message: str
    history: list[ChatMessage] = []


class SourceOut(BaseModel):
    """One retrieved chunk's source and similarity score, returned for transparency."""

    source: str
    score: float


class ChatResponse(BaseModel):
    """Response body for POST /chat."""

    reply: str
    sources: list[SourceOut]


def _qdrant(request: Request) -> QdrantClient:
    return request.app.state.qdrant


def _embedding_model(request: Request) -> SentenceTransformer:
    return request.app.state.embedding_model


def _llm_client(request: Request) -> OpenAI:
    return request.app.state.llm_client


def _vector_store(request: Request) -> VectorStoreConfig:
    return request.app.state.vector_store


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
    qdrant: QdrantClient = Depends(_qdrant),
    embedding_model: SentenceTransformer = Depends(_embedding_model),
    llm_client: OpenAI = Depends(_llm_client),
    llm_model: str = Depends(_llm_model),
    vector_store: VectorStoreConfig = Depends(_vector_store),
) -> ChatResponse:
    """Answer a question grounded in the caller's tenant's vectorized documents."""
    assert identity.org_id is not None  # resolve_identity() already guarantees this (401s otherwise)
    chunks = retrieve(qdrant, embedding_model, vector_store, identity.org_id, body.message)
    history = [{"role": m.role, "content": m.content} for m in body.history]
    reply = run_chat(llm_client, llm_model, chunks, history, body.message)
    sources = [SourceOut(source=c.source, score=c.score) for c in chunks]
    return ChatResponse(reply=reply, sources=sources)
