"""The source library: what a tenant's agent can search, scoped to that tenant's collection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from ai_circus_shared.auth import Identity
from ai_circus_shared.scenario_schema import VectorStoreConfig
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_agent.api import _qdrant, _scenario_definition, router
from rag_agent.core import library
from rag_agent.core.identity import resolve_identity
from rag_agent.core.library import list_sources, passages_of


def _point(source: str, text: str, chunk: int | None = None) -> SimpleNamespace:
    payload: dict[str, Any] = {"source": source, "text": text}
    if chunk is not None:
        payload["chunk"] = chunk
    return SimpleNamespace(payload=payload)


class FakeQdrant:
    """Qdrant's scroll over per-collection points, with its paging and the source filter."""

    def __init__(self, collections: dict[str, list[SimpleNamespace]]) -> None:
        """Hold the points of each collection."""
        self.collections = collections

    def collection_exists(self, name: str) -> bool:
        """Whether the collection exists."""
        return name in self.collections

    def scroll(
        self, collection_name: str, limit: int, offset: int | None = None, scroll_filter: Any = None, **_: Any
    ) -> tuple[list[SimpleNamespace], int | None]:
        """One page of points, filtered by source when asked."""
        points = self.collections[collection_name]
        if scroll_filter is not None:
            wanted = scroll_filter.must[0].match.value
            points = [p for p in points if p.payload["source"] == wanted]
        start = offset or 0
        end = start + limit
        return points[start:end], end if end < len(points) else None


COLLECTIONS = {
    "docs__org-1": [_point("a/one.md", "second", 1), _point("a/one.md", "first", 0), _point("b/two.md", "only", 0)],
    "docs__org-2": [_point("secret.md", "other tenant", 0)],
}


def test_sources_are_counted_across_scroll_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "SCROLL_PAGE", 2)
    sources, truncated = list_sources(FakeQdrant(COLLECTIONS), "docs__org-1")  # type: ignore[arg-type]
    assert sources == [{"source": "a/one.md", "passages": 2}, {"source": "b/two.md", "passages": 1}]
    assert truncated is False


def test_the_scan_cap_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "SCROLL_PAGE", 1)
    monkeypatch.setattr(library, "MAX_SCANNED_POINTS", 2)
    assert list_sources(FakeQdrant(COLLECTIONS), "docs__org-1")[1] is True  # type: ignore[arg-type]


def test_passages_come_in_document_order() -> None:
    assert passages_of(FakeQdrant(COLLECTIONS), "docs__org-1", "a/one.md") == ["first", "second"]  # type: ignore[arg-type]


def _client(org: str, vector_store: VectorStoreConfig | None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id=org, roles=frozenset())
    app.dependency_overrides[_scenario_definition] = lambda: SimpleNamespace(slug="docs", vector_store=vector_store)
    app.dependency_overrides[_qdrant] = lambda: FakeQdrant(COLLECTIONS)
    return TestClient(app)


STORE = VectorStoreConfig(backend="qdrant", collection_prefix="docs", top_k=3)


def test_each_tenant_sees_only_its_own_documents() -> None:
    mine = _client("org-1", STORE)
    assert [s["source"] for s in mine.get("/documents/docs").json()["sources"]] == ["a/one.md", "b/two.md"]
    assert mine.get("/documents/docs/passages", params={"source": "a/one.md"}).json()["passages"] == ["first", "second"]
    # Another tenant's document, by its exact name.
    assert mine.get("/documents/docs/passages", params={"source": "secret.md"}).status_code == 404
    assert _client("org-2", STORE).get("/documents/docs/passages", params={"source": "a/one.md"}).status_code == 404


def test_not_indexed_yet_and_no_documents() -> None:
    assert _client("org-3", STORE).get("/documents/docs").json() == {"sources": [], "truncated": False}
    assert _client("org-3", STORE).get("/documents/docs/passages", params={"source": "a"}).status_code == 404
    assert _client("org-1", None).get("/documents/docs").status_code == 404
    assert _client("org-1", STORE).get("/documents/docs/passages", params={"source": "x" * 501}).status_code == 422
