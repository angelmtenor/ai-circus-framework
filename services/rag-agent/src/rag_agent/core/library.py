"""
- Title:    The documents a tenant's assistant really searches
- Author:   Angel Martinez-Tenor

Reads the same per-tenant collection retrieval does (`scenario_schema.qdrant_collection_name`), so the
source library shows exactly what the agent can find: never a file it did not index. This service has
no object store and needs none. Both reads are bounded; a request costs a scroll, and nothing is kept.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

SCROLL_PAGE = 512
MAX_SCANNED_POINTS = 20_000
MAX_PASSAGES = 300


def list_sources(qdrant: QdrantClient, collection: str) -> tuple[list[dict[str, Any]], bool]:
    """Every source in the collection with its passage count, and whether the scan cap cut it short."""
    counts: Counter[str] = Counter()
    offset = None
    scanned = 0
    while scanned < MAX_SCANNED_POINTS:
        points, offset = qdrant.scroll(
            collection_name=collection,
            limit=SCROLL_PAGE,
            offset=offset,
            with_payload=["source"],
            with_vectors=False,
        )
        scanned += len(points)
        counts.update(str(p.payload["source"]) for p in points if p.payload and p.payload.get("source"))
        if offset is None:
            return [{"source": s, "passages": n} for s, n in sorted(counts.items())], False
    return [{"source": s, "passages": n} for s, n in sorted(counts.items())], True


def passages_of(qdrant: QdrantClient, collection: str, source: str) -> list[str]:
    """One source's indexed passages in document order (at most MAX_PASSAGES)."""
    points, _ = qdrant.scroll(
        collection_name=collection,
        scroll_filter=Filter(must=[FieldCondition(key="source", match=MatchValue(value=source))]),
        limit=MAX_PASSAGES,
        with_payload=True,
        with_vectors=False,
    )
    rows = [p.payload for p in points if p.payload and "text" in p.payload]
    # A collection indexed before `chunk` existed has no order to restore: the scan's order stands.
    rows.sort(key=lambda r: r.get("chunk", 0))
    return [str(r["text"]) for r in rows]
