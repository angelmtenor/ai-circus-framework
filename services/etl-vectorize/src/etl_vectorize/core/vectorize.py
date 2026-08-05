"""
- Title:    Document vectorization pipeline for conversational_rag scenarios
- Author:   ai-circus-framework contributors

Extract: bootstrap the tenant's documents into MinIO from the scenario's tracked
sample_docs/ folder on first run (demo convenience — a real deployment would have
each tenant upload their own documents instead). Transform: chunk + embed. Load:
upsert into the tenant's Qdrant collection for rag-agent to query.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from ai_circus_shared.scenario_schema import DocumentsConfig, VectorStoreConfig
from ai_circus_shared.storage import ObjectStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

from etl_vectorize.core.chunking import chunk_text
from etl_vectorize.core.logger import get_logger

logger = get_logger(__name__)


def collection_name(vector_store: VectorStoreConfig, org_id: str) -> str:
    """Per-tenant Qdrant collection name: '{collection_prefix}__{org_id}'."""
    return f"{vector_store.collection_prefix}__{org_id}"


def ensure_raw_docs(store: ObjectStore, org_id: str, documents: DocumentsConfig, scenario_dir: Path) -> None:
    """Upload the scenario's tracked sample documents to MinIO if the tenant has none yet."""
    if store.list(org_id, documents.raw_prefix):
        return

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


def build_points(docs: dict[str, str], documents: DocumentsConfig, model: SentenceTransformer) -> list[PointStruct]:
    """Chunk + embed every document; return Qdrant points ready to upsert."""
    points = []
    for source, text in docs.items():
        chunks = chunk_text(text, documents.chunking.chunk_size, documents.chunking.chunk_overlap)
        if not chunks:
            continue
        embeddings = model.encode(chunks, normalize_embeddings=True)
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            vector = [float(v) for v in embedding]
            points.append(PointStruct(id=str(uuid.uuid4()), vector=vector, payload={"text": chunk, "source": source}))
    return points


def upsert_points(client: QdrantClient, name: str, points: list[PointStruct], vector_size: int) -> None:
    """Create the tenant's collection if it doesn't exist yet, then upsert the given points."""
    if not client.collection_exists(name):
        client.create_collection(name, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))
    client.upsert(collection_name=name, points=points)


def run_vectorize(
    store: ObjectStore,
    qdrant: QdrantClient,
    model: SentenceTransformer,
    org_id: str,
    documents: DocumentsConfig,
    vector_store: VectorStoreConfig,
    scenario_dir: Path,
) -> int:
    """Run the full extract -> chunk -> embed -> load pipeline; return the upserted point count."""
    ensure_raw_docs(store, org_id, documents, scenario_dir)
    docs = load_raw_docs(store, org_id, documents)
    points = build_points(docs, documents, model)
    if points:
        vector_size = model.get_embedding_dimension()
        if vector_size is None:
            raise RuntimeError("Embedding model did not report a sentence embedding dimension.")
        name = collection_name(vector_store, org_id)
        upsert_points(qdrant, name, points, vector_size)
    logger.success("Vectorized {} document(s) into {} chunk(s) for org={}", len(docs), len(points), org_id)
    return len(points)
