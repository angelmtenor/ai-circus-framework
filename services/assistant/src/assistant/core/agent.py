"""
- Title:    AG-UI agent wrapper for chat-over-tabular-data
- Author:   ai-circus-framework contributors

`core/chat.py`'s bare `client.chat.completions.create` call has no tool-calling at
all, so it can't participate in generative UI (a frontend tool call, e.g.
render_chart/render_table, only reaches the model if the model is run as a
LangChain/LangGraph agent — see rag_agent.core.agent for the sibling
implementation this mirrors). This module gives the AG-UI endpoint (api.py's
`agui_endpoint`) a `create_agent`-based graph; the plain OpenAI-client path in
chat.py is untouched and still backs the legacy REST /chat/{scenario_slug} route.
"""

from __future__ import annotations

from typing import Any

from copilotkit import CopilotKitMiddleware, CopilotKitState
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import LLMResult
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph


class ModelUsageCallback(BaseCallbackHandler):
    """Captures the model that actually served the most recent completion in this
    run, straight off each response's `response_metadata["model_name"]`. litellm
    rewrites that field to the fallback model's name when litellm_config.yaml's
    `litellm_settings.fallbacks` kicks in, so comparing it against the requested
    model_name (see api.py's `_llm_model`) after the run is how a fallback is
    detected — never inferred/guessed, since a silent swap would mislead the user
    about which model actually answered.

    Built fresh per request (like rag_agent.core.agent's sibling implementation)
    and passed to `LangGraphAGUIAgent(config={"callbacks": [...]})` so concurrent
    requests never share state.
    """

    def __init__(self) -> None:
        """Start with no served model recorded — set on this run's first on_llm_end."""
        self.served_model: str | None = None

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Record the model_name off the last generation's response_metadata."""
        for generation in response.generations:
            for chunk in generation:
                message = getattr(chunk, "message", None)
                model_name = getattr(message, "response_metadata", {}).get("model_name") if message else None
                if model_name:
                    self.served_model = model_name


def build_agui_agent(llm: BaseChatModel, system_prompt: str, tools: list[BaseTool]) -> CompiledStateGraph:
    """Build the graph backing the AG-UI endpoint.

    `tools` (see api.py's `agui_endpoint`, built via `core.tools.build_prediction_tools`)
    give the model real dataset/prediction access alongside the static system-prompt
    grounding (see chat.py's build_system_prompt). `create_agent` + `CopilotKitMiddleware`
    is still the right shape for the *frontend*'s own generative-UI tool calls
    (render_chart/render_table), which arrive per-request as `RunAgentInput.tools`
    independent of `tools` here — same mix rag_agent.core.agent already runs with its
    `retrieve_docs` tool. A fresh `InMemorySaver` per request satisfies
    `ag_ui_langgraph`'s internal `graph.aget_state()` call (confirmed empirically on
    rag-agent: raises `ValueError: No checkpointer set` without one) — not for
    cross-request memory, since the AG-UI client resends the full message history on
    every run, same contract as `run_chat`.
    """
    return create_agent(
        llm,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[CopilotKitMiddleware()],
        # pyrefly: ignore [bad-argument-type]
        state_schema=CopilotKitState,
        checkpointer=InMemorySaver(),
    )
