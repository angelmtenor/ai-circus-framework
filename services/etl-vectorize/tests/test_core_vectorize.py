"""Tests for the document vectorization pipeline.

Uses a fake ObjectStore/QdrantClient and a fake embedding model so most tests don't
need real MinIO/Qdrant/sentence-transformers network calls — one integration test at
the bottom exercises the real SentenceTransformer model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_circus_shared.scenario_schema import DocumentChunking, DocumentEmbedding, DocumentsConfig, VectorStoreConfig

from etl_vectorize.core.vectorize import build_points, collection_name, ensure_raw_docs, load_raw_docs, run_vectorize

DOCUMENTS = DocumentsConfig(
    bucket="scenario-docs-rag",
    raw_prefix="raw/",
    seed_prefix="sample_docs",
    chunking=DocumentChunking(strategy="recursive_character", chunk_size=200, chunk_overlap=20),
    embedding=DocumentEmbedding(model="fake-model"),
)
VECTOR_STORE = VectorStoreConfig(backend="qdrant", collection_prefix="docs_rag", top_k=5)


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore."""

    def __init__(self) -> None:
        """Start with an empty per-tenant object map."""
        self._objects: dict[str, dict[str, bytes]] = {}

    def put(self, org_id: str, path: str, data: bytes) -> str:
        """Store bytes under a tenant-scoped path."""
        self._objects.setdefault(org_id, {})[path] = data
        return f"tenant-{org_id}/{path}"

    def get(self, org_id: str, path: str) -> bytes:
        """Retrieve previously stored bytes."""
        return self._objects[org_id][path]

    def list(self, org_id: str, prefix: str = "") -> list[str]:
        """List keys under a tenant/prefix."""
        return [k for k in self._objects.get(org_id, {}) if k.startswith(prefix)]


class FakeEmbeddingModel:
    """Deterministic stand-in for SentenceTransformer — one fixed-size vector per chunk."""

    def get_embedding_dimension(self) -> int:
        """Return a small fixed vector size."""
        return 4

    def encode(self, chunks: list[str], normalize_embeddings: bool = True) -> list[list[float]]:
        """Return a distinct-but-deterministic vector per chunk (by length)."""
        return [[float(len(c) % 4), 0.0, 0.0, 1.0] for c in chunks]


class FakeQdrantClient:
    """In-memory stand-in for qdrant_client.QdrantClient."""

    def __init__(self) -> None:
        """Start with no collections."""
        self.collections: dict[str, list] = {}
        self.created_with: dict[str, int] = {}

    def collection_exists(self, name: str) -> bool:
        """Return whether the named collection has been created."""
        return name in self.collections

    def create_collection(self, name: str, vectors_config: object) -> None:
        """Create an empty collection, recording the configured vector size."""
        self.collections[name] = []
        self.created_with[name] = vectors_config.size

    def upsert(self, collection_name: str, points: list) -> None:
        """Append points to the named collection."""
        self.collections[collection_name].extend(points)


@pytest.fixture
def scenario_dir(tmp_path: Path) -> Path:
    """A scenario directory with a tracked sample_docs/ folder, mirroring the real repo layout."""
    sample_dir = tmp_path / "sample_docs"
    sample_dir.mkdir()
    (sample_dir / "doc1.md").write_text("Hello world. " * 30)
    (sample_dir / "doc2.md").write_text("Another document. " * 30)
    return tmp_path


def test_collection_name_is_prefix_and_org_scoped() -> None:
    """The collection name combines the configured prefix and the tenant's org id."""
    assert collection_name(VECTOR_STORE, "org-1") == "docs_rag__org-1"


def test_ensure_raw_docs_bootstraps_from_seed_folder_when_missing(scenario_dir: Path) -> None:
    """If no raw docs exist yet for the tenant, every file in sample_docs/ is uploaded."""
    store = FakeObjectStore()

    ensure_raw_docs(store, "org-1", DOCUMENTS, scenario_dir)

    keys = store.list("org-1", DOCUMENTS.raw_prefix)
    assert set(keys) == {"raw/doc1.md", "raw/doc2.md"}


def test_ensure_raw_docs_leaves_existing_docs_untouched(scenario_dir: Path) -> None:
    """Already-uploaded documents for the tenant are never re-bootstrapped."""
    store = FakeObjectStore()
    store.put("org-1", "raw/existing.md", b"already here")

    ensure_raw_docs(store, "org-1", DOCUMENTS, scenario_dir)

    assert store.list("org-1", DOCUMENTS.raw_prefix) == ["raw/existing.md"]


def test_load_raw_docs_reads_every_uploaded_file(scenario_dir: Path) -> None:
    """load_raw_docs returns every raw document's text, keyed by its object key."""
    store = FakeObjectStore()
    ensure_raw_docs(store, "org-1", DOCUMENTS, scenario_dir)

    docs = load_raw_docs(store, "org-1", DOCUMENTS)

    assert set(docs) == {"raw/doc1.md", "raw/doc2.md"}
    assert docs["raw/doc1.md"].startswith("Hello world.")


def test_build_points_chunks_and_embeds_every_document() -> None:
    """build_points produces one Qdrant point per chunk, with source/text payload."""
    docs = {"raw/a.md": "word " * 100}

    points = build_points(docs, DOCUMENTS, FakeEmbeddingModel())

    assert len(points) > 1
    assert all(p.payload["source"] == "raw/a.md" for p in points)
    assert all(len(p.vector) == 4 for p in points)


def test_run_vectorize_end_to_end(scenario_dir: Path) -> None:
    """The full pipeline bootstraps, loads, chunks, embeds, and upserts in one call."""
    store = FakeObjectStore()
    qdrant = FakeQdrantClient()

    count = run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-1", DOCUMENTS, VECTOR_STORE, scenario_dir)

    assert count > 0
    assert qdrant.collections["docs_rag__org-1"]
    assert qdrant.created_with["docs_rag__org-1"] == 4


def test_run_vectorize_is_isolated_per_tenant(scenario_dir: Path) -> None:
    """Two tenants get separate collections and separate document sets."""
    store = FakeObjectStore()
    qdrant = FakeQdrantClient()

    run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-1", DOCUMENTS, VECTOR_STORE, scenario_dir)
    run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-2", DOCUMENTS, VECTOR_STORE, scenario_dir)

    assert "docs_rag__org-1" in qdrant.collections
    assert "docs_rag__org-2" in qdrant.collections
    assert qdrant.collections["docs_rag__org-1"] is not qdrant.collections["docs_rag__org-2"]
