"""
- Title:    Agentic RAG: the LLM decides whether retrieval is needed
- Author:   ai-circus-framework contributors

Replaces the earlier "always retrieve, then stuff into the prompt" design: the model
is given a `retrieve_docs` tool and a system prompt describing the scenario's domain
(`chat.context`), with instructions to call the tool only for in-domain questions and
answer chitchat/off-topic questions directly. Sources are captured via a mutable
closure on the tool rather than parsed out of the agent's internal state — simpler,
and robust to LangChain's own agent-loop implementation changing under us.
"""

from __future__ import annotations

from typing import Any

from ai_circus_shared.scenario_schema import VectorStoreConfig
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from rag_agent.core.retrieval import retrieve

SYSTEM_PROMPT_TEMPLATE = (
    "You are a helpful assistant. Your domain: {context}\n\n"
    "Call the retrieve_docs tool ONLY when the user's question relates to this domain "
    "— not for chitchat, greetings, or clearly unrelated questions, which you should "
    "answer directly without calling any tool. When retrieve_docs returns relevant "
    "excerpts, answer using ONLY those excerpts and cite the source file for each "
    "claim. If it returns no relevant documents, say so plainly rather than guessing."
)


def _build_retrieve_tool(
    qdrant: QdrantClient,
    embedder: SentenceTransformer,
    vector_store: VectorStoreConfig,
    org_id: str,
) -> tuple[StructuredTool, dict[str, list[dict[str, Any]]]]:
    """Build a retrieve_docs tool bound to this request's tenant/collection.

    Built fresh per request (not a module-level singleton) since it closes over
    per-request state (qdrant/embedder/vector_store/org_id all vary by request).
    `captured["sources"]` is populated as a side effect only if the agent actually
    calls the tool — staying absent is itself meaningful (the agent judged the
    question off-topic and answered without retrieval).
    """
    captured: dict[str, list[dict[str, Any]]] = {}

    def _retrieve(query: str) -> tuple[str, list[dict[str, Any]]]:
        chunks = retrieve(qdrant, embedder, vector_store, org_id, query)
        sources = [{"source": c.source, "score": c.score} for c in chunks]
        captured["sources"] = sources
        if not chunks:
            return "No relevant documents were found for this query.", sources
        content = "\n\n".join(f"[Source: {c.source}]\n{c.text}" for c in chunks)
        return content, sources

    tool = StructuredTool.from_function(
        func=_retrieve,
        name="retrieve_docs",
        description="Retrieve document excerpts to answer in-domain questions. Do not call this for chitchat.",
        response_format="content_and_artifact",
    )
    return tool, captured


def _to_lc_messages(history: list[dict[str, str]]) -> list[HumanMessage | AIMessage]:
    """Convert the API's plain role/content history into LangChain message objects."""
    return [
        HumanMessage(content=turn["content"]) if turn["role"] == "user" else AIMessage(content=turn["content"])
        for turn in history
    ]


def run_chat(
    llm: BaseChatModel,
    qdrant: QdrantClient,
    embedder: SentenceTransformer,
    vector_store: VectorStoreConfig,
    org_id: str,
    chat_context: str,
    history: list[dict[str, str]],
    message: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Run one agent turn; return (reply, sources) — sources is empty if no tool call happened."""
    tool, captured = _build_retrieve_tool(qdrant, embedder, vector_store, org_id)
    agent = create_agent(llm, tools=[tool], system_prompt=SYSTEM_PROMPT_TEMPLATE.format(context=chat_context.strip()))

    result = agent.invoke({"messages": [*_to_lc_messages(history), HumanMessage(content=message)]})
    reply = result["messages"][-1].content
    return reply, captured.get("sources", [])
