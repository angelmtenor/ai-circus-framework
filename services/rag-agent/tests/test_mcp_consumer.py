"""The chat as an MCP client (docs/adr/0019): tools loaded from a real door, the caller's token sent along."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from rag_agent.core import mcp_consumer
from rag_agent.core.mcp_consumer import consumable_servers, format_result, load_mcp_tools
from rag_agent.core.mcp_door import McpDoor, build_mcp_app
from tests.test_library import COLLECTIONS, FakeQdrant
from tests.test_mcp_door import _definition


@asynccontextmanager
async def _door_app() -> AsyncGenerator[FastAPI]:
    mcp_app = build_mcp_app()
    app = FastAPI()
    app.state.definitions = {"docs": _definition(["list_sources", "search_documents", "read_document"])}
    app.state.qdrant = FakeQdrant(COLLECTIONS)
    app.state.embedder = SimpleNamespace(encode_query=lambda _q: [0.1])
    app.mount("/mcp", McpDoor(mcp_app))
    async with mcp_app.lifespan(mcp_app):
        yield app


@pytest.fixture
def in_memory_doors(monkeypatch: pytest.MonkeyPatch) -> list[FastAPI]:
    """Point the consumer's MCP client at an in-process door app instead of loopback HTTP."""
    holder: list[FastAPI] = []

    def _client(base_url: str, server: str, authorization: str) -> Client:
        def _factory(**kwargs: Any) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=holder[0]), **kwargs)

        transport = StreamableHttpTransport(
            f"{base_url}/mcp/{server}", headers={"Authorization": authorization}, httpx_client_factory=_factory
        )
        return Client(transport, timeout=5)

    monkeypatch.setattr(mcp_consumer, "_client", _client)
    return holder


async def test_tools_come_from_the_door_and_answer_for_the_callers_tenant(
    in_memory_doors: list[FastAPI], retrieved: list[dict[str, Any]]
) -> None:
    async with _door_app() as app:
        in_memory_doors.append(app)
        tools = await load_mcp_tools(["docs"], "http://door", "Bearer t2")
        names = sorted(t.name for t in tools)
        assert names == ["mcp__docs__list_sources", "mcp__docs__read_document", "mcp__docs__search_documents"]
        search = next(t for t in tools if t.name.endswith("search_documents"))
        answer = await search.ainvoke({"query": "what", "top_k": 2})
    assert answer == '<retrieved_document source="a/one.md">\npassage for org-2\n</retrieved_document>'
    assert retrieved[0]["org_id"] == "org-2"  # the door resolved the tenant from the forwarded token


async def test_a_door_that_refuses_the_caller_contributes_no_tools(in_memory_doors: list[FastAPI]) -> None:
    async with _door_app() as app:
        in_memory_doors.append(app)
        assert await load_mcp_tools(["docs"], "http://door", "Bearer denied") == []


async def test_no_token_means_no_mcp_tools() -> None:
    assert await load_mcp_tools(["docs"], "http://door", None) == []


def test_only_listed_doors_served_here_with_tools_are_consumable() -> None:
    door = SimpleNamespace(mcp=SimpleNamespace(expose=["search_documents"], consume=[]))
    consumer_only = SimpleNamespace(mcp=SimpleNamespace(expose=[], consume=[]))
    chat = SimpleNamespace(mcp=SimpleNamespace(expose=[], consume=["door", "consumer_only", "elsewhere"]))
    served = {"door": door, "consumer_only": consumer_only}
    assert consumable_servers(chat, served) == ["door"]  # type: ignore[arg-type]
    assert consumable_servers(SimpleNamespace(mcp=None), served) == []  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("structured", "text", "is_error", "expected"),
    [
        (
            {"source": "a.md", "passages": ["one", "two"]},
            "",
            False,
            '<retrieved_document source="a.md">\none\n\ntwo\n</retrieved_document>',
        ),
        ({"passages": []}, "", False, "No relevant documents were found for this query."),
        ({"indexed": True, "sources": []}, "", False, '{"indexed": true, "sources": []}'),
        (None, "Document not found.", True, "The tool failed: Document not found."),
    ],
    ids=["read-document", "no-passages", "other-json", "error"],
)
def test_results_are_formatted_for_the_model(structured: Any, text: str, is_error: bool, expected: str) -> None:
    assert format_result(structured, text, is_error) == expected
