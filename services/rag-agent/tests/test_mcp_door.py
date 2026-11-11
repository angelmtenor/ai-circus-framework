"""The MCP door (docs/adr/0019): entitlement before MCP, tenant from the token, only exposed tools."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from ai_circus_shared.scenario_schema import VectorStoreConfig
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_agent.core import mcp_door
from rag_agent.core.mcp_door import McpDoor, build_mcp_app
from tests.test_library import COLLECTIONS, FakeQdrant

VECTOR_STORE = VectorStoreConfig(backend="qdrant", collection_prefix="docs", top_k=5)
HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


def _definition(expose: list[str] | None) -> SimpleNamespace:
    mcp = SimpleNamespace(expose=expose) if expose is not None else None
    return SimpleNamespace(slug="docs", vector_store=VECTOR_STORE, mcp=mcp)


@pytest.fixture
def client(retrieved: list[dict[str, Any]]) -> Iterator[TestClient]:
    mcp_app = build_mcp_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with mcp_app.lifespan(mcp_app):
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.definitions = {
        "docs": _definition(["list_sources", "search_documents", "read_document"]),
        "search_only": _definition(["search_documents"]),
        "no_door": _definition(None),
    }
    app.state.qdrant = FakeQdrant(COLLECTIONS)
    app.state.embedder = SimpleNamespace(encode_query=lambda _q: [0.1])
    app.mount("/mcp", McpDoor(mcp_app))
    with TestClient(app) as test_client:
        yield test_client


def _rpc(client: TestClient, slug: str, method: str, params: dict | None = None, auth: str = "Bearer t1") -> Any:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(f"/mcp/{slug}", json=body, headers={**HEADERS, "Authorization": auth})


def _call(client: TestClient, slug: str, tool: str, arguments: dict, auth: str = "Bearer t1") -> dict[str, Any]:
    response = _rpc(client, slug, "tools/call", {"name": tool, "arguments": arguments}, auth)
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_no_token_is_401_before_any_mcp_handling(client: TestClient) -> None:
    response = _rpc(client, "docs", "tools/list", auth="")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_a_tenant_not_entitled_to_the_scenario_is_403(client: TestClient) -> None:
    assert _rpc(client, "docs", "tools/list", auth="Bearer denied").status_code == 403


@pytest.mark.parametrize("slug", ["no_door", "unknown"])
def test_a_scenario_without_a_door_is_404(client: TestClient, slug: str) -> None:
    assert _rpc(client, slug, "tools/list").status_code == 404


def test_tools_list_shows_only_the_exposed_tools(client: TestClient) -> None:
    every = {t["name"] for t in _rpc(client, "docs", "tools/list").json()["result"]["tools"]}
    assert every == {"list_sources", "search_documents", "read_document"}
    only = [t["name"] for t in _rpc(client, "search_only", "tools/list").json()["result"]["tools"]]
    assert only == ["search_documents"]


def test_every_tool_is_annotated_read_only(client: TestClient) -> None:
    for tool in _rpc(client, "docs", "tools/list").json()["result"]["tools"]:
        assert tool["annotations"]["readOnlyHint"] is True


def test_a_tool_the_scenario_does_not_expose_cannot_be_called(client: TestClient) -> None:
    result = _call(client, "search_only", "read_document", {"source": "a/one.md"})
    assert result["isError"] is True


def test_search_is_scoped_to_the_tokens_tenant_and_capped(client: TestClient, retrieved: list) -> None:
    result = _call(client, "docs", "search_documents", {"query": "what", "top_k": 20}, auth="Bearer t2")
    assert result["structuredContent"]["passages"] == [
        {"source": "a/one.md", "score": 0.9123, "text": "passage for org-2"}
    ]
    assert retrieved == [{"org_id": "org-2", "top_k": 5, "query": "what"}]  # capped at vector_store.top_k


def test_an_over_long_query_is_rejected(client: TestClient, retrieved: list) -> None:
    result = _call(client, "docs", "search_documents", {"query": "x" * (mcp_door.MAX_QUERY_CHARS + 1)})
    assert result["isError"] is True
    assert retrieved == []


def test_list_sources_reads_only_the_callers_collection(client: TestClient) -> None:
    sources = _call(client, "docs", "list_sources", {})["structuredContent"]["sources"]
    assert [s["source"] for s in sources] == ["a/one.md", "b/two.md"]
    other = _call(client, "docs", "list_sources", {}, auth="Bearer t2")["structuredContent"]["sources"]
    assert [s["source"] for s in other] == ["secret.md"]


def test_list_sources_before_indexing_says_so(client: TestClient) -> None:
    result = _call(client, "docs", "list_sources", {}, auth="Bearer t3")["structuredContent"]
    assert result["indexed"] is False
    assert result["sources"] == []


def test_read_document_returns_passages_in_order(client: TestClient) -> None:
    result = _call(client, "docs", "read_document", {"source": "a/one.md"})["structuredContent"]
    assert result["passages"] == ["first", "second"]


def test_another_tenants_document_is_not_found(client: TestClient) -> None:
    assert _call(client, "docs", "read_document", {"source": "secret.md"})["isError"] is True


def test_an_unexpected_error_is_masked_from_the_client(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: Any) -> list:
        raise RuntimeError("qdrant at 10.0.0.7 refused the connection")

    monkeypatch.setattr(mcp_door, "retrieve", _boom)
    result = _call(client, "docs", "search_documents", {"query": "what"})
    assert result["isError"] is True
    assert "10.0.0.7" not in str(result)
