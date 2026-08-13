"""
- Title:    Document vectorization pipeline for conversational_rag scenarios
- Author:   ai-circus-framework contributors

Extract: bootstrap the tenant's documents into MinIO on first run, from either the
scenario's tracked sample_docs/ folder or a public GitHub repo folder (demo convenience
— a real deployment would have each tenant upload their own documents instead).
Transform: chunk + embed. Load: upsert into the tenant's Qdrant collection for
rag-agent to query.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
from ai_circus_shared.embeddings import EmbeddingProvider
from ai_circus_shared.scenario_schema import (
    DocumentsConfig,
    GithubDocsSource,
    VectorStoreConfig,
    qdrant_collection_name,
)
from ai_circus_shared.storage import ObjectStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from etl_vectorize.core.chunking import chunk_text
from etl_vectorize.core.logger import get_logger

logger = get_logger(__name__)

GITHUB_API_TIMEOUT_SECONDS = 15.0


def fetch_github_docs(source: GithubDocsSource) -> dict[str, bytes]:
    """Download every file under a public GitHub repo folder via the (unauthenticated)
    Contents API — fine for a public repo at demo-traffic volumes; GitHub caps
    unauthenticated requests at 60/hour per IP.
    """
    listing_url = f"https://api.github.com/repos/{source.repo}/contents/{source.path}"
    with httpx.Client(timeout=GITHUB_API_TIMEOUT_SECONDS) as client:
        listing = client.get(listing_url, params={"ref": source.ref})
        listing.raise_for_status()
        docs: dict[str, bytes] = {}
        for entry in listing.json():
            if entry["type"] != "file":
                continue
            content = client.get(entry["download_url"])
            content.raise_for_status()
            docs[entry["name"]] = content.content
        return docs


def ensure_raw_docs(store: ObjectStore, org_id: str, documents: DocumentsConfig, scenario_dir: Path) -> None:
    """Upload the scenario's bootstrap documents to MinIO if the tenant has none yet —
    from a public GitHub repo folder (`documents.github_source`) or a tracked local
    `sample_docs/`-style folder (`documents.seed_prefix`); `DocumentsConfig` guarantees
    exactly one of the two is set.
    """
    if store.list(org_id, documents.raw_prefix):
        return

    if documents.github_source is not None:
        logger.warning(
            "No documents found for org={} under {} — bootstrapping from github:{}/{} (demo convenience).",
            org_id,
            documents.raw_prefix,
            documents.github_source.repo,
            documents.github_source.path,
        )
        for name, content in fetch_github_docs(documents.github_source).items():
            store.put(org_id, f"{documents.raw_prefix}{name}", content)
        return

    assert documents.seed_prefix is not None  # guaranteed by DocumentsConfig's validator
    seed_dir = scenario_dir / documents.seed_prefix
    logger.warning(
        "No documents found for org={} under {} — bootstrapping from tracked seed folder {} (demo convenience).",
        org_id,
        documents.raw_prefix,
        seed_dir,
    )
    for path in sorted(seed_dir.glob("*")):
        if path.is_file():
            store.put(org_id, f"{documents.raw_prefix}{path.name}", path.read_bytes())


def load_raw_docs(store: ObjectStore, org_id: str, documents: DocumentsConfig) -> dict[str, str]:
    """Load every raw document for the tenant as {relative_key: text}."""
    keys = store.list(org_id, documents.raw_prefix)
    return {key: store.get(org_id, key).decode("utf-8") for key in keys}


def build_points(docs: dict[str, str], documents: DocumentsConfig, provider: EmbeddingProvider) -> list[PointStruct]:
    """Chunk + embed every document; return Qdrant points ready to upsert."""
    points = []
    for source, text in docs.items():
        chunks = chunk_text(text, documents.chunking.chunk_size, documents.chunking.chunk_overlap)
        if not chunks:
            continue
        embeddings = provider.encode_documents(chunks)
        for chunk, vector in zip(chunks, embeddings, strict=True):
            points.append(PointStruct(id=str(uuid.uuid4()), vector=vector, payload={"text": chunk, "source": source}))
    return points


def upsert_points(client: QdrantClient, name: str, points: list[PointStruct], vector_size: int) -> None:
    """(Re-)create the tenant's collection from scratch, then upsert the given points.

    `run_vectorize` reloads and re-embeds every document under the tenant's raw
    prefix on every run (not just changed ones), and each chunk gets a fresh random
    point id — upserting onto whatever's already in the collection would duplicate
    every unchanged chunk on each re-run and leave orphaned points forever for any
    document that was removed or renamed since the last run. Deleting the collection
    first makes each run's result an exact reflection of the tenant's current raw
    documents, including the (empty) case where every document was removed.
    """
    if client.collection_exists(name):
        client.delete_collection(name)
    client.create_collection(name, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))
    client.upsert(collection_name=name, points=points)


def run_vectorize(
    store: ObjectStore,
    qdrant: QdrantClient,
    provider: EmbeddingProvider,
    org_id: str,
    documents: DocumentsConfig,
    vector_store: VectorStoreConfig,
    scenario_dir: Path,
) -> int:
    """Run the full extract -> chunk -> embed -> load pipeline; return the upserted point count."""
    ensure_raw_docs(store, org_id, documents, scenario_dir)
    docs = load_raw_docs(store, org_id, documents)
    points = build_points(docs, documents, provider)
    name = qdrant_collection_name(vector_store, org_id)
    upsert_points(qdrant, name, points, provider.dimension)
    logger.success("Vectorized {} document(s) into {} chunk(s) for org={}", len(docs), len(points), org_id)
    return len(points)
