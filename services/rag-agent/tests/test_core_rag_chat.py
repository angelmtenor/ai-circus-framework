"""Tests for the RAG grounded-prompt building and completion call."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from rag_agent.core.rag_chat import build_grounded_prompt, chat
from rag_agent.core.retrieval import RetrievedChunk

CHUNKS = [
    RetrievedChunk(text="Overdraft fee is $25 per transaction.", source="raw/account_policies.md", score=0.9),
    RetrievedChunk(text="Customers can opt out of overdraft coverage.", source="raw/account_policies.md", score=0.8),
]


def test_build_grounded_prompt_includes_every_chunk_and_its_source() -> None:
    """The system prompt embeds each retrieved chunk's text and source file."""
    prompt = build_grounded_prompt(CHUNKS)

    assert "Overdraft fee is $25 per transaction." in prompt
    assert "Customers can opt out of overdraft coverage." in prompt
    assert prompt.count("raw/account_policies.md") == 2
    assert "ONLY the" in prompt


def test_build_grounded_prompt_with_no_chunks_says_so_plainly() -> None:
    """With no retrieved chunks, the prompt tells the model not to guess."""
    prompt = build_grounded_prompt([])

    assert "No relevant documents were found" in prompt


def test_chat_sends_grounded_system_prompt_and_returns_reply() -> None:
    """chat() assembles system+history+message and returns the completion's reply text."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="The overdraft fee is $25."))]
    )

    reply = chat(fake_client, "gpt-4o-mini", CHUNKS, [], "what is the overdraft fee?")

    assert reply == "The overdraft fee is $25."
    _, kwargs = fake_client.chat.completions.create.call_args
    assert kwargs["model"] == "gpt-4o-mini"
    assert "Overdraft fee is $25 per transaction." in kwargs["messages"][0]["content"]
    assert kwargs["messages"][-1] == {"role": "user", "content": "what is the overdraft fee?"}


def test_chat_returns_empty_string_for_none_content() -> None:
    """A completion with no content (e.g. a refusal) returns an empty string, not None."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
    )

    reply = chat(fake_client, "gpt-4o-mini", [], [], "hi")

    assert reply == ""
