"""Tests for retrieval over a tenant's vectorized documents."""

from __future__ import annotations

from types import SimpleNamespace

from ai_circus_shared.scenario_schema import VectorStoreConfig

from rag_agent.core.retrieval import RetrievedChunk, collection_name, retrieve

VECTOR_STORE = VectorStoreConfig(backend="qdrant", collection_prefix="docs_rag", top_k=3)


class FakeEmbeddingModel:
    """Deterministic stand-in for SentenceTransformer."""

    def encode(self, text: str, normalize_embeddings: bool = True) -> list[float]:
        """Return a fixed vector regardless of input."""
        return [0.1, 0.2, 0.3]


class FakeQdrantClient:
    """In-memory stand-in for qdrant_client.QdrantClient."""

    def __init__(self, *, has_collection: bool = True, points: list | None = None) -> None:
        """Configure whether the queried collection exists and what it returns."""
        self._has_collection = has_collection
        self._points = points or []
        self.query_calls: list[dict[str, object]] = []

    def collection_exists(self, name: str) -> bool:
        """Return the configured existence flag."""
        return self._has_collection

    def query_points(self, collection_name: str, query: list[float], limit: int) -> SimpleNamespace:
        """Record the call and return the configured fake points."""
        self.query_calls.append({"collection_name": collection_name, "query": query, "limit": limit})
        return SimpleNamespace(points=self._points)


def test_collection_name_is_prefix_and_org_scoped() -> None:
    """The collection name combines the configured prefix and the tenant's org id (matches etl-vectorize)."""
    assert collection_name(VECTOR_STORE, "org-1") == "docs_rag__org-1"


def test_retrieve_returns_empty_list_when_collection_missing() -> None:
    """A tenant with no vectorized documents yet (no collection) gets no results, not an error."""
    qdrant = FakeQdrantClient(has_collection=False)

    chunks = retrieve(qdrant, FakeEmbeddingModel(), VECTOR_STORE, "org-1", "what is the overdraft fee?")

    assert chunks == []
    assert qdrant.query_calls == []


def test_retrieve_queries_the_tenant_scoped_collection_with_top_k() -> None:
    """retrieve() queries the org-scoped collection name with the configured top_k limit."""
    qdrant = FakeQdrantClient(has_collection=True, points=[])

    retrieve(qdrant, FakeEmbeddingModel(), VECTOR_STORE, "org-1", "question")

    assert qdrant.query_calls == [{"collection_name": "docs_rag__org-1", "query": [0.1, 0.2, 0.3], "limit": 3}]


def test_retrieve_maps_qdrant_points_to_retrieved_chunks() -> None:
    """Qdrant's payload/score fields are mapped onto RetrievedChunk."""
    payload = {"text": "Overdraft fee is $25.", "source": "raw/account_policies.md"}
    fake_point = SimpleNamespace(payload=payload, score=0.87)
    qdrant = FakeQdrantClient(has_collection=True, points=[fake_point])

    chunks = retrieve(qdrant, FakeEmbeddingModel(), VECTOR_STORE, "org-1", "overdraft fee?")

    assert chunks == [RetrievedChunk(text="Overdraft fee is $25.", source="raw/account_policies.md", score=0.87)]
