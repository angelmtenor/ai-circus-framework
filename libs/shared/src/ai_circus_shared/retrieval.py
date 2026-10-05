"""
- Title:    Top-k retrieval over a tenant's vectorized documents
- Author:   Angel Martinez-Tenor

The one reader of the per-tenant chunk collection etl-vectorize writes
(`scenario_schema.qdrant_collection_name`): rag-agent's `retrieve_docs`, form-agent's
catalog lookup and assistant's document tool (a `tabular_ml` scenario's
`documents.tool`) all call `retrieve`, and `format_retrieved` is the one place the
`<retrieved_document source="…">` delimiters are written — the tags ui-react's
ChatPanel reads sources from and every agent's prompt tells the model to treat as
untrusted data.

Typed against a two-method `Protocol` rather than `qdrant_client.QdrantClient`, so this
package does not have to depend on qdrant-client; every caller passes a real client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ai_circus_shared.embeddings import EmbeddingProvider
from ai_circus_shared.scenario_schema import VectorStoreConfig, qdrant_collection_name


class VectorSearchClient(Protocol):
    """The two `QdrantClient` methods retrieval uses."""

    def collection_exists(self, collection_name: str, /) -> bool:
        """Whether the collection exists."""
        ...

    def query_points(self, collection_name: str, *, query: Any, limit: int) -> Any:
        """Nearest neighbours of `query` (a `QueryResponse` with `.points`)."""
        ...


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieved chunk: its text, source document, and similarity score."""

    text: str
    source: str
    score: float


def retrieve(
    qdrant: VectorSearchClient,
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


def format_retrieved(chunks: list[RetrievedChunk]) -> str:
    """Wrap each chunk in `<retrieved_document source="…">` tags, so the model can tell
    retrieved (untrusted) text from its own instructions and the UI can list sources."""
    return "\n\n".join(f'<retrieved_document source="{c.source}">\n{c.text}\n</retrieved_document>' for c in chunks)
