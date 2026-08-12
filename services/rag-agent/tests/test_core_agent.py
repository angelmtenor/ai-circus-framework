"""Tests for the agentic RAG chat: the retrieve_docs tool and the AG-UI agent builder."""

from __future__ import annotations

from ai_circus_shared.scenario_schema import VectorStoreConfig
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from rag_agent.core.agent import build_agui_agent, build_retrieve_tool
from rag_agent.core.retrieval import RetrievedChunk

VECTOR_STORE = VectorStoreConfig(backend="qdrant", collection_prefix="docs_rag", top_k=3)


class FakeChatModel(BaseChatModel):
    """A minimal fake chat model — enough for create_agent's binding, never actually invoked here."""

    def bind_tools(self, tools: object, **kwargs: object) -> FakeChatModel:
        """Tool binding is a no-op here — the fake ignores the tool schema entirely."""
        return self

    def _generate(
        self, messages: object, stop: object = None, run_manager: object = None, **kwargs: object
    ) -> ChatResult:
        """Never called in these tests — build_agui_agent only compiles the graph, it doesn't run it."""
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

    @property
    def _llm_type(self) -> str:
        """Identify this fake model type for LangChain's internals."""
        return "fake-chat-model"


def test_build_retrieve_tool_returns_content_and_sources(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """A query that matches real chunks returns cited content and captures its sources."""
    monkeypatch.setattr(
        "rag_agent.core.agent.retrieve",
        lambda *_a, **_kw: [RetrievedChunk(text="ATM limit is $1000.", source="policy.md", score=0.9)],
    )
    tool, captured = build_retrieve_tool(object(), object(), VECTOR_STORE, "org-1")

    result = tool.func("ATM withdrawal limit")

    assert result[0] == "[Source: policy.md]\nATM limit is $1000."
    assert result[1] == [{"source": "policy.md", "score": 0.9}]
    assert captured["sources"] == [{"source": "policy.md", "score": 0.9}]


def test_build_retrieve_tool_reports_no_results_without_erroring(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """A query with no matching chunks returns a plain "not found" message and empty sources."""
    monkeypatch.setattr("rag_agent.core.agent.retrieve", lambda *_a, **_kw: [])
    tool, captured = build_retrieve_tool(object(), object(), VECTOR_STORE, "org-1")

    result = tool.func("unrelated question")

    assert result == ("No relevant documents were found for this query.", [])
    assert captured["sources"] == []


def test_build_retrieve_tool_is_scoped_to_the_calling_org(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """Each tool closes over the org_id it was built for — retrieve() is called with that tenant, not another."""
    seen_org_ids: list[str] = []
    monkeypatch.setattr(
        "rag_agent.core.agent.retrieve",
        lambda _qdrant, _embedder, _vector_store, org_id, _query: (seen_org_ids.append(org_id) or []),
    )
    tool, _captured = build_retrieve_tool(object(), object(), VECTOR_STORE, "org-42")

    tool.func("anything")

    assert seen_org_ids == ["org-42"]


def test_build_agui_agent_compiles_with_the_given_tools() -> None:
    """build_agui_agent compiles without error and binds the given tools onto the graph."""
    tool, _captured = build_retrieve_tool(object(), object(), VECTOR_STORE, "org-1")

    graph = build_agui_agent(FakeChatModel(), [tool], "bank policies")

    assert graph is not None
