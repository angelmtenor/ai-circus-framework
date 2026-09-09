"""
- Title:    Form-filling agent graph
- Author:   ai-circus-framework contributors

The model's only server-side tool is `retrieve_catalog` (built when the scenario
configures classification — see `build_catalog_retrieve_tool`); the tool that
actually fills the form, `update_form_fields`, is declared by the *frontend*
(`ui-react`'s `formAssistUi.tsx`, via `useCopilotAction`) and arrives per-request in
`RunAgentInput.tools` — `CopilotKitMiddleware` turns a call to it into an AG-UI event
for the client to handle, the same way `render_chart`/`render_table` already work for
`tabular_ml`/`conversational_rag`. No Python implementation of it exists here.
"""

from __future__ import annotations

from typing import Any

from ai_circus_shared.embeddings import EmbeddingProvider
from ai_circus_shared.scenario_schema import VectorStoreConfig
from copilotkit import CopilotKitMiddleware, CopilotKitState
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import LLMResult
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from qdrant_client import QdrantClient

from form_agent.core.retrieval import retrieve


class ModelUsageCallback(BaseCallbackHandler):
    """Captures the model that actually served the most recent completion in this
    run, straight off each response's `response_metadata["model_name"]`. litellm
    rewrites that field to the fallback model's name when litellm_config.yaml's
    `litellm_settings.fallbacks` kicks in, so comparing it against the requested
    model_name (see api.py's `_llm_model_name`) after the run is how a fallback is
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


def build_catalog_retrieve_tool(
    qdrant: QdrantClient,
    embedder: EmbeddingProvider,
    vector_store: VectorStoreConfig,
    org_id: str,
) -> StructuredTool:
    """Build a retrieve_catalog tool bound to this request's tenant/collection.

    Built fresh per request (not a module-level singleton) since it closes over
    per-request state — same pattern as rag_agent.core.agent.build_retrieve_tool.
    """

    def _retrieve(query: str) -> str:
        chunks = retrieve(qdrant, embedder, vector_store, org_id, query)
        if not chunks:
            return "No matching catalog entry was found for this query."
        return "\n\n".join(f'<catalog_entry source="{c.source}">\n{c.text}\n</catalog_entry>' for c in chunks)

    return StructuredTool.from_function(
        func=_retrieve,
        name="retrieve_catalog",
        description=(
            "Look up the catalog of request types to figure out which one matches what the user described, "
            "and what extra fields/requirements that type has."
        ),
    )


def build_agui_agent(llm: BaseChatModel, system_prompt: str, tools: list[BaseTool]) -> CompiledStateGraph:
    """Build the graph backing the AG-UI endpoint (see api.py's `agui_endpoint`).

    `tools` is `[retrieve_catalog]` for a classification-driven scenario, or `[]` for
    a plain slot-filling one — `update_form_fields` is never in this list (see module
    docstring). See rag_agent.core.agent.build_agui_agent for why CopilotKitMiddleware/
    CopilotKitState/checkpointer are needed.
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
