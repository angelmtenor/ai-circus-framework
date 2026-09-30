"""
- Title:    Document vectorization pipeline for conversational_rag scenarios
- Author:   Angel Martinez-Tenor

Extract: bootstrap the tenant's documents into SeaweedFS on first run, from either the
scenario's tracked sample_docs/ folder or a public GitHub repo folder (demo convenience
— a real deployment would have each tenant upload their own documents instead).
Transform: chunk + embed. Load: upsert into the tenant's Qdrant collection for
rag-agent to query.

A scenario whose documents ship a knowledge graph (`documents.knowledge_graph`) also
gets it bootstrapped into the tenant's bucket and indexed — every node and every
relation embedded as one point of the tenant's `kg_collection_name` collection, which is
both rag-agent's seed-linking index and its copy of the graph (see its graph_retrieval.py).
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
from ai_circus_shared.embeddings import EmbeddingProvider
from ai_circus_shared.network_graph import NetworkGraph, parse_graph
from ai_circus_shared.scenario_schema import (
    KNOWLEDGE_GRAPH_DOCUMENT_KIND,
    KNOWLEDGE_GRAPH_KEY,
    KNOWLEDGE_GRAPH_SYNONYM_KIND,
    DocumentsConfig,
    GithubDocsSource,
    VectorStoreConfig,
    kg_collection_name,
    qdrant_collection_name,
)
from ai_circus_shared.storage import ObjectStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from etl_vectorize.core.chunking import chunk_text
from etl_vectorize.core.logger import get_logger

logger = get_logger(__name__)

GITHUB_API_TIMEOUT_SECONDS = 15.0
SYNONYM_THRESHOLD = 0.85  # cosine; measured on aml_regulation_kg, where 0.85+ same-class pairs are rewordings
SYNONYM_MAX_PER_NODE = 2
KNOWLEDGE_GRAPH_SEED_MARKER = "kg/seed.sha256"  # SHA-256 of the seed a tenant's graph was bootstrapped from


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


def _bootstrap_from_seed_dir(store: ObjectStore, org_id: str, documents: DocumentsConfig, scenario_dir: Path) -> None:
    """Upload every file in the scenario's tracked local seed folder to SeaweedFS."""
    assert documents.seed_prefix is not None  # only called when set
    seed_dir = scenario_dir / documents.seed_prefix
    for path in sorted(seed_dir.glob("*")):
        if path.is_file():
            store.put(org_id, f"{documents.raw_prefix}{path.name}", path.read_bytes())


def ensure_raw_docs(store: ObjectStore, org_id: str, documents: DocumentsConfig, scenario_dir: Path) -> None:
    """Upload the scenario's bootstrap documents to SeaweedFS if the tenant has none yet —
    from a public GitHub repo folder (`documents.github_source`) or a tracked local
    `sample_docs/`-style folder (`documents.seed_prefix`); `DocumentsConfig` guarantees
    at least one of the two is set. If both are set, `github_source` is tried first and
    a failure (rate limit, outage, no network) falls back to `seed_prefix` instead of
    leaving the tenant with zero documents — see `DocumentsConfig`'s docstring.
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
        try:
            docs = fetch_github_docs(documents.github_source)
        except httpx.HTTPError as e:
            if documents.seed_prefix is None:
                raise
            logger.warning(
                "GitHub fetch failed ({}) — falling back to tracked local seed folder {}.",
                e,
                scenario_dir / documents.seed_prefix,
            )
            _bootstrap_from_seed_dir(store, org_id, documents, scenario_dir)
            return
        for name, content in docs.items():
            store.put(org_id, f"{documents.raw_prefix}{name}", content)
        return

    assert documents.seed_prefix is not None  # guaranteed by DocumentsConfig's validator
    logger.warning(
        "No documents found for org={} under {} — bootstrapping from tracked seed folder {} (demo convenience).",
        org_id,
        documents.raw_prefix,
        scenario_dir / documents.seed_prefix,
    )
    _bootstrap_from_seed_dir(store, org_id, documents, scenario_dir)


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


def ensure_knowledge_graph(store: ObjectStore, org_id: str, documents: DocumentsConfig, scenario_dir: Path) -> None:
    """Keep the tenant's knowledge graph in step with the scenario's tracked seed file —
    the same demo bootstrap as `ensure_raw_docs`, validated before it is stored.

    Unlike the documents, a graph bootstrapped from the seed is *replaced* when the seed
    changes (a re-extraction, a fix), recognised by the seed's SHA-256 stored next to it
    (`KNOWLEDGE_GRAPH_SEED_MARKER`). A graph with no marker is the tenant's own and is
    never overwritten — unless it is byte-identical to the seed (bootstrapped before the
    marker existed), which is then adopted.
    """
    assert documents.knowledge_graph is not None  # only called when set
    data = (scenario_dir / documents.knowledge_graph.seed_file).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if store.exists(org_id, KNOWLEDGE_GRAPH_KEY):
        if store.exists(org_id, KNOWLEDGE_GRAPH_SEED_MARKER):
            if store.get(org_id, KNOWLEDGE_GRAPH_SEED_MARKER).decode() == digest:
                return
            logger.warning("The scenario's seed knowledge graph changed — updating org={}'s copy.", org_id)
        elif store.get(org_id, KNOWLEDGE_GRAPH_KEY) == data:
            store.put(org_id, KNOWLEDGE_GRAPH_SEED_MARKER, digest.encode())
            return
        else:
            return  # the tenant's own graph
    else:
        logger.warning("No knowledge graph found for org={} — bootstrapping it from the scenario's seed file.", org_id)
    parse_graph(data)
    store.put(org_id, KNOWLEDGE_GRAPH_KEY, data)
    store.put(org_id, KNOWLEDGE_GRAPH_SEED_MARKER, digest.encode())


def build_graph_points(graph: NetworkGraph, provider: EmbeddingProvider) -> list[PointStruct]:
    """One point per node ("label (type): description") and per relation ("A relation B"),
    payloads carrying everything rag-agent needs to rebuild the graph from the collection.
    Ids are derived from the element, so a re-run replaces rather than duplicates.
    """
    labels = {node.id: node.label or node.id for node in graph.nodes}
    texts: list[str] = []
    payloads: list[dict[str, Any]] = []
    for node in graph.nodes:
        detail = f": {node.description}" if node.description and node.description != node.label else ""
        texts.append(f"{labels[node.id]} ({node.type or node.kind}){detail}")
        payloads.append({
            "element": "node",
            "id": node.id,
            "label": labels[node.id],
            "type": node.type or "",
            "description": node.description or "",
            "citation": node.citation or "",
        })
    for edge in graph.edges:
        texts.append(f"{labels[edge.source]} {edge.label or edge.kind.replace('_', ' ')} {labels[edge.target]}")
        payloads.append({
            "element": "edge",
            "source": edge.source,
            "target": edge.target,
            "kind": edge.kind,
            "citation": edge.citation or "",
            "evidence": edge.evidence or "",
        })
    if not texts:
        return []
    vectors = provider.encode_documents(texts)
    node_vectors = dict(zip([node.id for node in graph.nodes], vectors, strict=False))
    for source, target, vector in synonym_pairs(graph, node_vectors):
        payloads.append({"element": "edge", "source": source, "target": target, "kind": KNOWLEDGE_GRAPH_SYNONYM_KIND})
        vectors.append(vector)
    return [
        PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, repr(sorted(p.items())))), vector=vector, payload=p)
        for p, vector in zip(payloads, vectors, strict=True)
    ]


def synonym_pairs(graph: NetworkGraph, vectors: dict[str, list[float]]) -> list[tuple[str, str, list[float]]]:
    """HippoRAG 2's synonymy edges: concepts of one class whose embeddings are at least
    SYNONYM_THRESHOLD apart ("Report suspicion to FIU" / "Report suspicious transactions to
    FIU", extracted from two articles), at most SYNONYM_MAX_PER_NODE each. Only relevance
    spreads along them — never a citable fact. Document nodes and labels with digits
    ("5 years" / "1 year": numbers embed alike) are left out. Each edge's vector is the
    mean of its ends, so no extra embedding call is needed.
    """
    documents = {e.target for e in graph.edges if e.kind == KNOWLEDGE_GRAPH_DOCUMENT_KIND}
    concepts = [
        n for n in graph.nodes if n.id in vectors and n.id not in documents and not re.search(r"\d", n.label or n.id)
    ]
    unit = {n.id: _normalised(vectors[n.id]) for n in concepts}
    candidates: list[tuple[float, str, str]] = []
    for i, a in enumerate(concepts):
        for b in concepts[i + 1 :]:
            if a.type == b.type:
                similarity = sum(x * y for x, y in zip(unit[a.id], unit[b.id], strict=True))
                if similarity >= SYNONYM_THRESHOLD:
                    candidates.append((similarity, a.id, b.id))
    linked: dict[str, int] = {}
    existing = {frozenset((e.source, e.target)) for e in graph.edges}
    pairs = []
    for _, a, b in sorted(candidates, reverse=True):
        if linked.get(a, 0) >= SYNONYM_MAX_PER_NODE or linked.get(b, 0) >= SYNONYM_MAX_PER_NODE:
            continue
        if frozenset((a, b)) in existing:
            continue
        linked[a] = linked.get(a, 0) + 1
        linked[b] = linked.get(b, 0) + 1
        pairs.append((a, b, [(x + y) / 2 for x, y in zip(vectors[a], vectors[b], strict=True)]))
    return pairs


def _normalised(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


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
    if documents.knowledge_graph is not None:
        ensure_knowledge_graph(store, org_id, documents, scenario_dir)
        graph = parse_graph(store.get(org_id, KNOWLEDGE_GRAPH_KEY))
        graph_points = build_graph_points(graph, provider)
        upsert_points(qdrant, kg_collection_name(vector_store, org_id), graph_points, provider.dimension)
        logger.success(
            "Indexed the knowledge graph ({} nodes, {} relations) for org={}",
            len(graph.nodes),
            len(graph.edges),
            org_id,
        )
    return len(points)
