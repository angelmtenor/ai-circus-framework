"""
- Title:    Retrieval over a tenant's vectorized document catalog
- Author:   ai-circus-framework contributors

Only used for scenarios that set `form.classification_field` (and therefore
`documents`/`vector_store` — enforced by scenario_schema's own validator); a plain
slot-filling `assisted_form` scenario with no classification concept never calls this.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_circus_shared.embeddings import EmbeddingProvider
from ai_circus_shared.scenario_schema import VectorStoreConfig, qdrant_collection_name
from qdrant_client import QdrantClient


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieved chunk: its text, source document, and similarity score."""

    text: str
    source: str
    score: float


def retrieve(
    qdrant: QdrantClient,
    provider: EmbeddingProvider,
    vector_store: VectorStoreConfig,
    org_id: str,
    query: str,
) -> list[RetrievedChunk]:
    """Embed the query and return the tenant's top-k most similar chunks."""
    name = qdrant_collection_name(vector_store, org_id)
    if not qdrant.collection_exists(name):
        return []

    query_vector = provider.encode_query(query)
    results = qdrant.query_points(collection_name=name, query=query_vector, limit=vector_store.top_k).points

    chunks = []
    for r in results:
        if r.payload is None:
            continue
        chunks.append(RetrievedChunk(text=r.payload["text"], source=r.payload["source"], score=r.score))
    return chunks
