"""
- Title:    RAG chat: grounded prompt from retrieved chunks + llm-gateway call
- Author:   ai-circus-framework contributors

Agent-based RAG: retrieval happens first (see retrieval.py), then the retrieved
chunks are injected into the system prompt so the model answers strictly from the
tenant's own documents — no arbitrary code execution, and the model never falls back
to unretrieved general knowledge without saying so.
"""

from __future__ import annotations

from typing import cast

from openai import OpenAI
from openai.types.chat import ChatCompletion

from rag_agent.core.retrieval import RetrievedChunk


def build_grounded_prompt(chunks: list[RetrievedChunk]) -> str:
    """Ground the assistant in the retrieved chunks, or say plainly that none were found."""
    if not chunks:
        return (
            "You are a helpful assistant. No relevant documents were found in the "
            "knowledge base for this question — say so plainly rather than guessing."
        )
    context = "\n\n".join(f"[Source: {c.source}]\n{c.text}" for c in chunks)
    return (
        "You are a helpful assistant. Answer the user's question using ONLY the "
        "retrieved document excerpts below. If the answer isn't contained in them, "
        "say so plainly rather than guessing — do not use outside knowledge. Cite the "
        "source file for each claim.\n\n"
        f"{context}"
    )


def chat(
    client: OpenAI,
    model: str,
    chunks: list[RetrievedChunk],
    history: list[dict[str, str]],
    message: str,
) -> str:
    """Send the grounded conversation to llm-gateway and return the assistant's reply text."""
    system_prompt = build_grounded_prompt(chunks)
    messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": message}]
    response = cast(ChatCompletion, client.chat.completions.create(model=model, messages=messages, stream=False))
    return response.choices[0].message.content or ""
