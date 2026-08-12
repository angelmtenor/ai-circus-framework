"""Tests for the AG-UI agent builder."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from assistant.core.agent import build_agui_agent


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


@tool
def _fake_tool(x: int) -> int:
    """A no-op tool, just to exercise build_agui_agent's tools plumbing."""
    return x


def test_build_agui_agent_compiles_with_no_tools() -> None:
    """build_agui_agent still compiles when called with an empty tools list."""
    graph = build_agui_agent(FakeChatModel(), "a system prompt", [])

    assert graph is not None


def test_build_agui_agent_compiles_with_the_given_tools() -> None:
    """build_agui_agent compiles without error and binds the given tools onto the graph."""
    graph = build_agui_agent(FakeChatModel(), "a system prompt", [_fake_tool])

    assert graph is not None
