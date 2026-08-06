"""Tests for the agentic RAG chat: the LLM decides whether to call retrieve_docs."""

from __future__ import annotations

from ai_circus_shared.scenario_schema import VectorStoreConfig
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult

from rag_agent.core.agent import run_chat
from rag_agent.core.retrieval import RetrievedChunk

VECTOR_STORE = VectorStoreConfig(backend="qdrant", collection_prefix="docs_rag", top_k=3)


class FakeToolCallingModel(BaseChatModel):
    """A minimal fake chat model that supports tool binding and returns fixed responses in order."""

    responses: list[AIMessage]
    calls: int = 0

    def bind_tools(self, tools: object, **kwargs: object) -> FakeToolCallingModel:
        """Tool binding is a no-op here — the fake ignores the tool schema entirely."""
        return self

    def _generate(
        self, messages: object, stop: object = None, run_manager: object = None, **kwargs: object
    ) -> ChatResult:
        """Return the next canned response in sequence."""
        message = self.responses[self.calls]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        """Identify this fake model type for LangChain's internals."""
        return "fake-tool-calling-model"


def test_chitchat_question_answers_directly_without_calling_the_tool(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """A chitchat message never triggers retrieval — sources come back empty."""
    monkeypatch.setattr(
        "rag_agent.core.agent.retrieve",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("retrieve() should not be called for chitchat")),
    )
    model = FakeToolCallingModel(responses=[AIMessage(content="Hi there! How can I help?")])

    reply, sources = run_chat(model, object(), object(), VECTOR_STORE, "org-1", "bank policies", [], "hi there!")

    assert reply == "Hi there! How can I help?"
    assert sources == []


def test_in_domain_question_calls_the_tool_and_returns_sources(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """An in-domain question triggers retrieval; the response is grounded and sources are returned."""
    monkeypatch.setattr(
        "rag_agent.core.agent.retrieve",
        lambda *_a, **_kw: [RetrievedChunk(text="ATM limit is $1000.", source="policy.md", score=0.9)],
    )
    tool_call = ToolCall(name="retrieve_docs", args={"query": "ATM withdrawal limit"}, id="call_1")
    model = FakeToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[tool_call]),
            AIMessage(content="The ATM limit is $1000, per policy.md."),
        ]
    )

    reply, sources = run_chat(
        model, object(), object(), VECTOR_STORE, "org-1", "bank policies", [], "what's the ATM withdrawal limit?"
    )

    assert reply == "The ATM limit is $1000, per policy.md."
    assert sources == [{"source": "policy.md", "score": 0.9}]


def test_tool_call_with_no_matching_chunks_returns_empty_sources(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """The tool is called but finds nothing — sources is an empty list, not omitted."""
    monkeypatch.setattr("rag_agent.core.agent.retrieve", lambda *_a, **_kw: [])
    tool_call = ToolCall(name="retrieve_docs", args={"query": "unrelated"}, id="call_1")
    model = FakeToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[tool_call]),
            AIMessage(content="I couldn't find anything relevant to that in the documents."),
        ]
    )

    reply, sources = run_chat(model, object(), object(), VECTOR_STORE, "org-1", "bank policies", [], "what about X?")

    assert "couldn't find" in reply
    assert sources == []


def test_prior_history_is_forwarded_to_the_model(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """Prior conversation turns are converted to LangChain messages ahead of the new question."""
    monkeypatch.setattr("rag_agent.core.agent.retrieve", lambda *_a, **_kw: [])
    model = FakeToolCallingModel(responses=[AIMessage(content="Sure, following up on that...")])
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello, how can I help?"}]

    reply, _sources = run_chat(model, object(), object(), VECTOR_STORE, "org-1", "bank policies", history, "and then?")

    assert reply == "Sure, following up on that..."
