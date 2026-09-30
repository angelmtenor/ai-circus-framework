"""Tests for the document vectorization pipeline.

Uses a fake ObjectStore/QdrantClient and a fake embedding model so most tests don't
need real SeaweedFS/Qdrant/sentence-transformers network calls — one integration test at
the bottom exercises the real SentenceTransformer model.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from ai_circus_shared.scenario_schema import (
    DocumentChunking,
    DocumentEmbedding,
    DocumentsConfig,
    GithubDocsSource,
    VectorStoreConfig,
)

from etl_vectorize.core import vectorize
from etl_vectorize.core.vectorize import (
    build_points,
    ensure_raw_docs,
    fetch_github_docs,
    load_raw_docs,
    run_vectorize,
)

DOCUMENTS = DocumentsConfig(
    bucket="scenario-docs-rag",
    raw_prefix="raw/",
    seed_prefix="sample_docs",
    chunking=DocumentChunking(strategy="recursive_character", chunk_size=200, chunk_overlap=20),
    embedding=DocumentEmbedding(model="fake-model"),
)
DOCUMENTS_FROM_GITHUB = DocumentsConfig(
    bucket="scenario-ai-circus-reference",
    raw_prefix="raw/",
    github_source=GithubDocsSource(repo="owner/repo", path="reference", ref="develop"),
    chunking=DocumentChunking(strategy="recursive_character", chunk_size=200, chunk_overlap=20),
    embedding=DocumentEmbedding(model="fake-model"),
)
DOCUMENTS_FROM_GITHUB_WITH_LOCAL_FALLBACK = DocumentsConfig(
    bucket="scenario-ai-circus-reference",
    raw_prefix="raw/",
    seed_prefix="sample_docs",
    github_source=GithubDocsSource(repo="owner/repo", path="reference", ref="develop"),
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

    def exists(self, org_id: str, path: str) -> bool:
        """Whether a tenant-scoped path holds an object."""
        return path in self._objects.get(org_id, {})

    def list(self, org_id: str, prefix: str = "") -> list[str]:
        """List keys under a tenant/prefix."""
        return [k for k in self._objects.get(org_id, {}) if k.startswith(prefix)]


class FakeEmbeddingModel:
    """Deterministic stand-in for an EmbeddingProvider — one fixed-size vector per chunk."""

    dimension = 4

    def encode_documents(self, chunks: list[str]) -> list[list[float]]:
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

    def delete_collection(self, name: str) -> None:
        """Drop a collection entirely."""
        del self.collections[name]
        del self.created_with[name]

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


def test_fetch_github_docs_downloads_every_file_in_the_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_github_docs lists a GitHub folder via the Contents API, then downloads each file (skipping subdirs)."""
    listing = [
        {
            "name": "a.md",
            "type": "file",
            "download_url": "https://raw.githubusercontent.com/owner/repo/develop/reference/a.md",
        },
        {
            "name": "b.md",
            "type": "file",
            "download_url": "https://raw.githubusercontent.com/owner/repo/develop/reference/b.md",
        },
        {"name": "subdir", "type": "dir", "download_url": None},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://api.github.com/"):
            assert request.url.params["ref"] == "develop"
            return httpx.Response(200, json=listing)
        if str(request.url).endswith("a.md"):
            return httpx.Response(200, content=b"content a")
        return httpx.Response(200, content=b"content b")

    real_client_cls = httpx.Client
    monkeypatch.setattr(
        vectorize.httpx, "Client", lambda **_kwargs: real_client_cls(transport=httpx.MockTransport(handler))
    )

    docs = fetch_github_docs(GithubDocsSource(repo="owner/repo", path="reference", ref="develop"))

    assert docs == {"a.md": b"content a", "b.md": b"content b"}


def test_ensure_raw_docs_bootstraps_from_github_source_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When documents.github_source is set, ensure_raw_docs fetches from GitHub instead of a local folder."""
    store = FakeObjectStore()
    monkeypatch.setattr(vectorize, "fetch_github_docs", lambda source: {"a.md": b"content a", "b.md": b"content b"})

    ensure_raw_docs(store, "org-1", DOCUMENTS_FROM_GITHUB, Path("/does/not/exist"))

    assert store.get("org-1", "raw/a.md") == b"content a"
    assert store.get("org-1", "raw/b.md") == b"content b"


def test_ensure_raw_docs_falls_back_to_seed_folder_when_github_fetch_fails(
    scenario_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If github_source is set alongside a seed_prefix fallback, a failed GitHub fetch
    (rate limit, outage, ...) bootstraps from the local seed folder instead of raising.
    """
    store = FakeObjectStore()

    def raise_rate_limited(source: GithubDocsSource) -> dict[str, bytes]:
        raise httpx.HTTPStatusError(
            "429 Too Many Requests", request=httpx.Request("GET", "https://x"), response=httpx.Response(429)
        )

    monkeypatch.setattr(vectorize, "fetch_github_docs", raise_rate_limited)

    ensure_raw_docs(store, "org-1", DOCUMENTS_FROM_GITHUB_WITH_LOCAL_FALLBACK, scenario_dir)

    keys = store.list("org-1", DOCUMENTS_FROM_GITHUB_WITH_LOCAL_FALLBACK.raw_prefix)
    assert set(keys) == {"raw/doc1.md", "raw/doc2.md"}


def test_ensure_raw_docs_reraises_github_failure_when_no_local_fallback_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a seed_prefix fallback configured, a failed GitHub fetch must still raise —
    silently leaving the tenant with zero documents would be worse than a loud failure.
    """
    store = FakeObjectStore()

    def raise_rate_limited(source: GithubDocsSource) -> dict[str, bytes]:
        raise httpx.HTTPStatusError(
            "429 Too Many Requests", request=httpx.Request("GET", "https://x"), response=httpx.Response(429)
        )

    monkeypatch.setattr(vectorize, "fetch_github_docs", raise_rate_limited)

    with pytest.raises(httpx.HTTPStatusError):
        ensure_raw_docs(store, "org-1", DOCUMENTS_FROM_GITHUB, Path("/does/not/exist"))


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


def test_run_vectorize_does_not_duplicate_points_on_rerun(scenario_dir: Path) -> None:
    """Re-running against the same (unchanged) documents must not grow the collection —
    each chunk gets a fresh random point id, so upserting onto a not-cleared
    collection would duplicate every chunk on every run.
    """
    store = FakeObjectStore()
    qdrant = FakeQdrantClient()

    first_count = run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-1", DOCUMENTS, VECTOR_STORE, scenario_dir)
    run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-1", DOCUMENTS, VECTOR_STORE, scenario_dir)

    assert len(qdrant.collections["docs_rag__org-1"]) == first_count


def test_run_vectorize_removes_points_for_deleted_documents(scenario_dir: Path) -> None:
    """A document removed from the tenant's raw docs since the last run must not
    leave its old chunks retrievable forever.
    """
    store = FakeObjectStore()
    qdrant = FakeQdrantClient()

    run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-1", DOCUMENTS, VECTOR_STORE, scenario_dir)
    for key in store.list("org-1", DOCUMENTS.raw_prefix):
        if key.endswith("doc2.md"):
            del store._objects["org-1"][key]

    run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-1", DOCUMENTS, VECTOR_STORE, scenario_dir)

    remaining_sources = {p.payload["source"] for p in qdrant.collections["docs_rag__org-1"]}
    assert remaining_sources == {"raw/doc1.md"}


def test_run_vectorize_is_isolated_per_tenant(scenario_dir: Path) -> None:
    """Two tenants get separate collections and separate document sets."""
    store = FakeObjectStore()
    qdrant = FakeQdrantClient()

    run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-1", DOCUMENTS, VECTOR_STORE, scenario_dir)
    run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-2", DOCUMENTS, VECTOR_STORE, scenario_dir)

    assert "docs_rag__org-1" in qdrant.collections
    assert "docs_rag__org-2" in qdrant.collections
    assert qdrant.collections["docs_rag__org-1"] is not qdrant.collections["docs_rag__org-2"]


KNOWLEDGE_GRAPH = {
    "nodes": [
        {"id": "oe", "kind": "entity", "label": "Obliged entity", "type": "ObligedEntity"},
        {
            "id": "report",
            "kind": "entity",
            "label": "Report suspicion",
            "type": "Obligation",
            "description": "Tell the FIU.",
        },
        {"id": "art69", "kind": "entity", "label": "Art. 69", "type": "Article", "citation": "doc1.md"},
    ],
    "edges": [
        {
            "source": "oe",
            "target": "report",
            "kind": "must_perform",
            "label": "must perform",
            "citation": "AMLR Art. 69(1)",
            "evidence": "shall report",
        },
        {"source": "report", "target": "art69", "kind": "cited_in"},
    ],
}


def _kg_documents() -> DocumentsConfig:
    from ai_circus_shared.scenario_schema import KnowledgeGraphConfig

    return DOCUMENTS.model_copy(update={"knowledge_graph": KnowledgeGraphConfig(seed_file="kg.json")})


def test_run_vectorize_bootstraps_and_indexes_the_knowledge_graph_per_tenant(scenario_dir: Path) -> None:
    """A knowledge-graph scenario's graph lands in the tenant's bucket and every node and
    relation becomes one point of the tenant's kg collection, with rebuildable payloads.
    """
    import json

    (scenario_dir / "kg.json").write_text(json.dumps(KNOWLEDGE_GRAPH))
    store = FakeObjectStore()
    qdrant = FakeQdrantClient()

    run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-1", _kg_documents(), VECTOR_STORE, scenario_dir)
    run_vectorize(store, qdrant, FakeEmbeddingModel(), "org-1", _kg_documents(), VECTOR_STORE, scenario_dir)

    assert store.exists("org-1", "kg/graph.json")
    points = qdrant.collections["docs_rag_kg__org-1"]
    assert len(points) == 5  # 3 nodes + 2 relations, not duplicated by the re-run
    payloads = [p.payload for p in points]
    assert {
        "element": "edge",
        "source": "oe",
        "target": "report",
        "kind": "must_perform",
        "citation": "AMLR Art. 69(1)",
        "evidence": "shall report",
    } in payloads
    assert next(p for p in payloads if p.get("id") == "art69")["citation"] == "doc1.md"
    assert "docs_rag_kg__org-2" not in qdrant.collections


def test_ensure_knowledge_graph_keeps_a_tenants_own_graph_and_rejects_an_invalid_seed(scenario_dir: Path) -> None:
    """An existing tenant graph is never overwritten; a broken seed never reaches the bucket."""
    import json

    from etl_vectorize.core.vectorize import ensure_knowledge_graph

    (scenario_dir / "kg.json").write_text(json.dumps(KNOWLEDGE_GRAPH))
    store = FakeObjectStore()
    store.put("org-1", "kg/graph.json", b"tenant's own")
    ensure_knowledge_graph(store, "org-1", _kg_documents(), scenario_dir)
    assert store.get("org-1", "kg/graph.json") == b"tenant's own"

    (scenario_dir / "kg.json").write_text('{"nodes": [{"id": "x", "kind": "entity"}]}')  # entity without label
    with pytest.raises(ValueError, match="label"):
        ensure_knowledge_graph(store, "org-2", _kg_documents(), scenario_dir)
    assert not store.exists("org-2", "kg/graph.json")


def test_synonym_pairs_link_rewordings_of_one_class_but_never_numbers_or_documents() -> None:
    """HippoRAG 2's synonymy edges: near-identical same-class concepts are linked (the
    edge vector is the mean of its ends); numbers, other classes and documents are not.
    """
    from ai_circus_shared.network_graph import NetworkGraph

    from etl_vectorize.core.vectorize import synonym_pairs

    graph = NetworkGraph.model_validate({
        "nodes": [
            {"id": "a", "kind": "entity", "label": "Report suspicion to FIU", "type": "Obligation"},
            {"id": "b", "kind": "entity", "label": "Report suspicious transactions to FIU", "type": "Obligation"},
            {"id": "c", "kind": "entity", "label": "FIU", "type": "Authority"},
            {"id": "y1", "kind": "entity", "label": "5 years", "type": "Limit"},
            {"id": "y2", "kind": "entity", "label": "1 year", "type": "Limit"},
            {"id": "doc", "kind": "entity", "label": "Art. 69", "type": "Article", "citation": "x.md"},
        ],
        "edges": [{"source": "a", "target": "doc", "kind": "cited_in"}],
    })
    same = [1.0, 0.0]
    vectors = {"a": same, "b": [0.99, 0.05], "c": same, "y1": same, "y2": same, "doc": same}

    pairs = synonym_pairs(graph, vectors)

    assert [(a, b) for a, b, _ in pairs] == [("a", "b")]
    assert pairs[0][2] == [0.995, 0.025]


def test_ensure_knowledge_graph_follows_a_changed_seed_but_adopts_or_keeps_the_rest(scenario_dir: Path) -> None:
    """A seed-bootstrapped graph is replaced when the seed changes (no manual clean-up);
    a pre-marker copy identical to the seed is adopted; a tenant's own graph stays.
    """
    import json

    from etl_vectorize.core.vectorize import KNOWLEDGE_GRAPH_SEED_MARKER, ensure_knowledge_graph

    seed = scenario_dir / "kg.json"
    seed.write_text(json.dumps(KNOWLEDGE_GRAPH))
    store = FakeObjectStore()
    ensure_knowledge_graph(store, "org-1", _kg_documents(), scenario_dir)
    assert store.get("org-1", "kg/graph.json") == seed.read_bytes()

    changed = {**KNOWLEDGE_GRAPH, "nodes": [*KNOWLEDGE_GRAPH["nodes"], {"id": "x", "kind": "entity", "label": "X"}]}
    seed.write_text(json.dumps(changed))
    ensure_knowledge_graph(store, "org-1", _kg_documents(), scenario_dir)
    assert store.get("org-1", "kg/graph.json") == seed.read_bytes()

    store.put("org-2", "kg/graph.json", seed.read_bytes())  # bootstrapped before the marker existed
    ensure_knowledge_graph(store, "org-2", _kg_documents(), scenario_dir)
    assert store.exists("org-2", KNOWLEDGE_GRAPH_SEED_MARKER)
