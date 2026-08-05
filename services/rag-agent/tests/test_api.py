"""Tests for the /chat FastAPI endpoint, with all dependencies overridden by fakes."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from ai_circus_shared.auth import Identity
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_agent.api import _embedding_model, _llm_client, _llm_model, _qdrant, _vector_store, router
from rag_agent.core.identity import resolve_identity


@pytest.fixture
def fake_llm_client() -> MagicMock:
    """A mock OpenAI client returning a fixed completion."""
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="The overdraft fee is $25."))]
    )
    return client


@pytest.fixture
def client(fake_llm_client: MagicMock) -> Generator[TestClient]:
    """A TestClient with identity/qdrant/embedding-model/llm-client dependencies overridden."""
    app = FastAPI()
    app.include_router(router)

    fake_point = SimpleNamespace(
        payload={"text": "Overdraft fee is $25.", "source": "raw/account_policies.md"}, score=0.9
    )

    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:docs_rag"})
    )
    app.dependency_overrides[_qdrant] = lambda: SimpleNamespace(
        collection_exists=lambda _name: True,
        query_points=lambda **_kwargs: SimpleNamespace(points=[fake_point]),
    )
    app.dependency_overrides[_embedding_model] = lambda: SimpleNamespace(encode=lambda _q, **_kw: [0.1, 0.2])
    app.dependency_overrides[_llm_client] = lambda: fake_llm_client
    app.dependency_overrides[_llm_model] = lambda: "gpt-4o-mini"
    from ai_circus_shared.scenario_schema import VectorStoreConfig

    app.dependency_overrides[_vector_store] = lambda: VectorStoreConfig(
        backend="qdrant", collection_prefix="docs_rag", top_k=3
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_healthz(client: TestClient) -> None:
    """/healthz reports ok."""
    assert client.get("/healthz").json() == {"status": "ok"}


def test_chat_returns_reply_and_sources(client: TestClient) -> None:
    """POST /chat returns the completion's reply text plus the retrieved sources."""
    response = client.post("/chat", json={"message": "what is the overdraft fee?"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "The overdraft fee is $25."
    assert body["sources"] == [{"source": "raw/account_policies.md", "score": 0.9}]


def test_chat_forwards_history(client: TestClient, fake_llm_client: MagicMock) -> None:
    """Prior conversation turns are forwarded to the completion call in order."""
    response = client.post(
        "/chat",
        json={
            "message": "and then?",
            "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    _, kwargs = fake_llm_client.chat.completions.create.call_args
    assert kwargs["messages"][1:3] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
