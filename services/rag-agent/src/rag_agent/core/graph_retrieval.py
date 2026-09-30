"""
- Title:    Graph retrieval over a tenant's knowledge graph (HippoRAG 2-style GraphRAG)
- Author:   Angel Martinez-Tenor

A `conversational_rag` scenario may ship a knowledge graph with its documents
(`documents.knowledge_graph`): typed concepts, relations that cite an article and quote
it, and one node per source document. etl-vectorize indexes every node and every
relation ("A must perform B") in the tenant's `kg_collection_name` collection; this
module reads that collection back (the whole graph, a few hundred points, TTL-cached per
tenant) and answers a question the way HippoRAG 2 does:

1. **Seed linking** — the query embedding is matched against nodes *and* relations
   (query-to-triple matching); each hit's nodes become seeds, weighted by similarity.
   Synonymy edges etl-vectorize inferred between rewordings of one concept extracted
   from different articles let relevance cross from one wording to the other.
2. **Personalized PageRank** from those seeds over the undirected graph, restricted to
   `hops` of the seeds — relevance spreads along relations and through document nodes,
   so a concept two hops away that several seeds point at outranks a lone near-match.
3. **Context** — the relations with the highest PageRank mass (capped at `max_triples`)
   as one compact line each, plus the best `max_passages` chunks of the highest-ranked
   documents, so the answer can quote the law. The cap is the point: the LLM reads a
   small, relevant subgraph rather than every chunk that looked similar.

Pure Python on purpose: the graphs are tiny (≤ 2,000 nodes by contract), so a
power-iteration PageRank and a BFS need no numeric or graph library.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from ai_circus_shared.embeddings import EmbeddingProvider
from ai_circus_shared.network_graph import MAX_GRAPH_EDGES, MAX_GRAPH_NODES
from ai_circus_shared.scenario_schema import (
    KNOWLEDGE_GRAPH_DOCUMENT_KIND,
    KNOWLEDGE_GRAPH_SYNONYM_KIND,
    DocumentsConfig,
    KnowledgeGraphConfig,
    VectorStoreConfig,
    kg_collection_name,
    qdrant_collection_name,
)
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

DOCUMENT_EDGE_KIND = KNOWLEDGE_GRAPH_DOCUMENT_KIND  # concept -> the document node it was extracted from
SYNONYM_EDGE_KIND = KNOWLEDGE_GRAPH_SYNONYM_KIND  # near-identical concepts (inferred by etl-vectorize)
STRUCTURAL_KINDS = {DOCUMENT_EDGE_KIND, SYNONYM_EDGE_KIND}  # steer PageRank, never quoted as facts
SEED_HITS = 8  # nodes + relations matched against the whole question
CONCEPT_HITS = 5  # ... and against each key concept the agent names
MAX_CONCEPTS = 4
DAMPING = 0.5  # HippoRAG's choice: half the mass restarts at the seeds, keeping it local
PAGERANK_ITERATIONS = 40
CACHE_TTL_SECONDS = 300.0
CACHE_MAX_ENTRIES = 32
MAX_PATHS = 3


@dataclass(frozen=True)
class KgNode:
    """One concept (or document) node."""

    id: str
    label: str
    type: str
    description: str = ""
    citation: str = ""  # for a document node: its file name under documents.raw_prefix


@dataclass(frozen=True)
class KgEdge:
    """One directed, typed relation."""

    source: str
    target: str
    kind: str
    citation: str = ""
    evidence: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        """(source, kind, target) — how the UI trace names an edge."""
        return (self.source, self.kind, self.target)


@dataclass
class KnowledgeGraph:
    """A tenant's knowledge graph, with an undirected adjacency index for traversal."""

    nodes: dict[str, KgNode]
    edges: list[KgEdge]
    adjacency: dict[str, list[tuple[str, KgEdge]]] = field(init=False)
    by_key: dict[tuple[str, str, str], KgEdge] = field(init=False)
    documents: set[str] = field(init=False)

    def __post_init__(self) -> None:
        """Index every edge in both directions (relevance flows either way), by key, and
        which nodes stand for a source document (the targets of `cited_in` edges).
        """
        self.adjacency = defaultdict(list)
        for edge in self.edges:
            self.adjacency[edge.source].append((edge.target, edge))
            self.adjacency[edge.target].append((edge.source, edge))
        self.by_key = {edge.key: edge for edge in self.edges}
        self.documents = {
            e.target for e in self.edges if e.kind == DOCUMENT_EDGE_KIND and self.nodes[e.target].citation
        }

    def document_count(self, node_id: str) -> int:
        """How many source documents a concept was extracted from."""
        return sum(1 for neighbour, edge in self.adjacency.get(node_id, []) if edge.kind == DOCUMENT_EDGE_KIND)

    def is_document(self, node_id: str) -> bool:
        """True for the nodes that stand for a source document."""
        return node_id in self.documents


@dataclass
class GraphTrace:
    """What the graph tools retrieved during one run — sent to the UI to light up."""

    seeds: set[str] = field(default_factory=set)
    nodes: set[str] = field(default_factory=set)
    edges: set[tuple[str, str, str]] = field(default_factory=set)

    def as_event(self) -> dict[str, Any]:
        """JSON-able payload of the `knowledge_graph_trace` AG-UI custom event."""
        return {
            "seeds": sorted(self.seeds),
            "nodes": sorted(self.nodes),
            "edges": [list(edge) for edge in sorted(self.edges)],
        }


class KnowledgeGraphCache:
    """Per-collection (so per-tenant) TTL cache of loaded graphs, bounded in size."""

    def __init__(self, ttl_seconds: float = CACHE_TTL_SECONDS, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        """Start empty."""
        self._ttl = ttl_seconds
        self._max = max_entries
        self._data: dict[str, tuple[KnowledgeGraph | None, float]] = {}
        self._lock = threading.Lock()

    def get(self, qdrant: QdrantClient, collection: str) -> KnowledgeGraph | None:
        """The graph in `collection`, loading it on a miss; None if the tenant has none."""
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(collection)
        if entry is not None and entry[1] > now:
            return entry[0]
        graph = load_graph(qdrant, collection)
        with self._lock:
            if len(self._data) >= self._max and collection not in self._data:
                del self._data[next(iter(self._data))]
            self._data[collection] = (graph, now + self._ttl)
        return graph


def load_graph(qdrant: QdrantClient, collection: str) -> KnowledgeGraph | None:
    """Read a whole knowledge-graph collection back into memory (payloads only)."""
    if not qdrant.collection_exists(collection):
        return None
    nodes: dict[str, KgNode] = {}
    edges: list[KgEdge] = []
    offset: Any = None
    while len(nodes) + len(edges) <= MAX_GRAPH_NODES + MAX_GRAPH_EDGES:
        points, offset = qdrant.scroll(collection, limit=512, offset=offset, with_payload=True, with_vectors=False)
        for point in points:
            payload = point.payload or {}
            if payload.get("element") == "node":
                nodes[payload["id"]] = KgNode(
                    id=payload["id"],
                    label=payload.get("label") or payload["id"],
                    type=payload.get("type") or "",
                    description=payload.get("description") or "",
                    citation=payload.get("citation") or "",
                )
            elif payload.get("element") == "edge":
                edges.append(
                    KgEdge(
                        source=payload["source"],
                        target=payload["target"],
                        kind=payload["kind"],
                        citation=payload.get("citation") or "",
                        evidence=payload.get("evidence") or "",
                    )
                )
        if offset is None:
            break
    edges = [e for e in edges if e.source in nodes and e.target in nodes]
    return KnowledgeGraph(nodes=nodes, edges=edges)


def within_hops(graph: KnowledgeGraph, seeds: set[str], hops: int) -> set[str]:
    """Every node at most `hops` relations away from a seed (BFS, undirected)."""
    reached = set(seeds)
    frontier = deque((seed, 0) for seed in seeds)
    while frontier:
        node, depth = frontier.popleft()
        if depth == hops:
            continue
        for neighbour, _ in graph.adjacency.get(node, []):
            if neighbour not in reached:
                reached.add(neighbour)
                frontier.append((neighbour, depth + 1))
    return reached


def personalized_pagerank(
    graph: KnowledgeGraph, seeds: dict[str, float], allowed: set[str], damping: float = DAMPING
) -> dict[str, float]:
    """PageRank restarting at `seeds` (weights normalised), over the subgraph `allowed`."""
    total = sum(seeds.values()) or 1.0
    restart = {node: weight / total for node, weight in seeds.items() if node in allowed}
    degree = {node: sum(1 for n, _ in graph.adjacency.get(node, []) if n in allowed) for node in allowed}
    rank = dict(restart)
    for _ in range(PAGERANK_ITERATIONS):
        spread: dict[str, float] = defaultdict(float)
        for node, mass in rank.items():
            if degree[node] == 0:
                spread[node] += mass  # a dangling node keeps its mass
                continue
            share = mass / degree[node]
            for neighbour, _ in graph.adjacency[node]:
                if neighbour in allowed:
                    spread[neighbour] += share
        rank = {node: (1 - damping) * restart.get(node, 0.0) + damping * spread.get(node, 0.0) for node in allowed}
    return rank


def link_seeds(
    qdrant: QdrantClient, collection: str, graph: KnowledgeGraph, vectors: list[tuple[list[float], int]]
) -> dict[str, float]:
    """Seed weights from the nearest nodes and relations of each (vector, hit limit).

    A relation hit seeds both its ends. Each seed is weighted by similarity and by its
    *specificity* (HippoRAG): divided by the square root of how many documents it appears
    in, so a concept every article mentions ("Obliged entity") does not drown the rest.
    """
    seeds: dict[str, float] = defaultdict(float)
    for vector, limit in vectors:
        hits = qdrant.query_points(collection_name=collection, query=vector, limit=limit, with_payload=True)
        for hit in hits.points:
            payload = hit.payload or {}
            score = max(float(hit.score), 0.0)
            if payload.get("element") == "node" and payload.get("id") in graph.nodes:
                ends = [payload["id"]]
            elif (
                payload.get("element") == "edge"
                and (payload["source"], payload["kind"], payload["target"]) in graph.by_key
            ):
                ends = [payload["source"], payload["target"]]
            else:
                continue
            for node in ends:
                seeds[node] += score / math.sqrt(max(graph.document_count(node), 1))
    return dict(seeds)


def _line(graph: KnowledgeGraph, edge: KgEdge) -> str:
    """One compact, citable relation line."""
    source, target = graph.nodes[edge.source].label, graph.nodes[edge.target].label
    cite = f" [{edge.citation}]" if edge.citation else ""
    return f"{source} --{edge.kind}--> {target}{cite}"


def graph_search(
    qdrant: QdrantClient,
    embedder: EmbeddingProvider,
    graph_cache: KnowledgeGraphCache,
    documents: DocumentsConfig,
    vector_store: VectorStoreConfig,
    org_id: str,
    query: str,
    trace: GraphTrace,
    concepts: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """HippoRAG 2-style retrieval: (tool content, sources) — see the module docstring.

    `concepts` are the distinct things the question asks about, named by the agent in
    the same tool call ("report suspicious transaction", "FIU", "deadline"): a
    multi-part question's single embedding lands on one part only, while each concept
    links its own seeds — measured on aml_regulation_kg, it is what brings the reporting
    articles into "what must the bank do, towards whom, and how fast?".
    """
    config: KnowledgeGraphConfig | None = documents.knowledge_graph
    collection = kg_collection_name(vector_store, org_id)
    graph = graph_cache.get(qdrant, collection)
    if config is None or graph is None or not graph.nodes:
        return "The knowledge graph is not available for this tenant yet.", []

    query_vector = embedder.encode_query(query)
    vectors = [(query_vector, SEED_HITS)]
    for concept in (concepts or [])[:MAX_CONCEPTS]:
        if concept.strip():
            vectors.append((embedder.encode_query(concept.strip()[:120]), CONCEPT_HITS))
    seeds = link_seeds(qdrant, collection, graph, vectors)
    if not seeds:
        return "No concept in the knowledge graph matches this question.", []
    allowed = within_hops(graph, set(seeds), config.hops)
    rank = personalized_pagerank(graph, seeds, allowed)

    # Relations between two reached concepts, ranked by the PageRank mass of their ends
    # normalised by degree — a hub ("Credit institution") keeps mass by sheer connectivity
    # and would otherwise fill every line with its own relations. Document and synonymy
    # links only steer the ranking.
    candidates = [
        e for e in graph.edges if e.kind not in STRUCTURAL_KINDS and e.source in allowed and e.target in allowed
    ]
    degree: dict[str, int] = defaultdict(int)
    for e in candidates:
        degree[e.source] += 1
        degree[e.target] += 1

    def mass(node: str) -> float:
        return rank.get(node, 0.0) / math.sqrt(max(degree[node], 1))

    candidates.sort(key=lambda e: -(mass(e.source) + mass(e.target)))
    chosen = candidates[: config.max_triples]

    trace.seeds |= set(seeds)
    trace.edges |= {e.key for e in chosen}
    trace.nodes |= {n for e in chosen for n in (e.source, e.target)}

    # The source documents with the most PageRank mass — their best chunks for this query.
    ranked_docs = sorted((n for n in allowed if graph.is_document(n)), key=lambda n: -rank.get(n, 0.0))
    doc_ids = ranked_docs[: config.max_passages]
    trace.nodes |= set(doc_ids)
    passages = _passages(qdrant, documents, vector_store, org_id, graph, doc_ids, query_vector, config.max_passages)

    lines = "\n".join(_line(graph, e) for e in chosen)
    content = f"<knowledge_graph>\n{lines}\n</knowledge_graph>"
    if passages:
        content += "\n\n" + "\n\n".join(
            f'<retrieved_document source="{source}">\n{text}\n</retrieved_document>' for source, text, _ in passages
        )
    sources = [{"source": source, "score": score} for source, _, score in passages]
    return content, sources


def _passages(
    qdrant: QdrantClient,
    documents: DocumentsConfig,
    vector_store: VectorStoreConfig,
    org_id: str,
    graph: KnowledgeGraph,
    doc_ids: list[str],
    query_vector: list[float],
    limit: int,
) -> list[tuple[str, str, float]]:
    """The query's best chunks among the given document nodes' files: (source, text, score)."""
    collection = qdrant_collection_name(vector_store, org_id)
    if limit == 0 or not doc_ids or not qdrant.collection_exists(collection):
        return []
    sources = [f"{documents.raw_prefix}{graph.nodes[d].citation}" for d in doc_ids]
    hits = qdrant.query_points(
        collection_name=collection,
        query=query_vector,
        query_filter=Filter(must=[FieldCondition(key="source", match=MatchAny(any=sources))]),
        limit=limit,
        with_payload=True,
    )
    return [
        (point.payload["source"], point.payload["text"], float(point.score)) for point in hits.points if point.payload
    ]


def graph_path(
    qdrant: QdrantClient,
    embedder: EmbeddingProvider,
    graph_cache: KnowledgeGraphCache,
    vector_store: VectorStoreConfig,
    org_id: str,
    from_concept: str,
    to_concept: str,
    trace: GraphTrace,
) -> str:
    """Up to MAX_PATHS shortest relation chains between the two concepts' best matches."""
    collection = kg_collection_name(vector_store, org_id)
    graph = graph_cache.get(qdrant, collection)
    if graph is None or not graph.nodes:
        return "The knowledge graph is not available for this tenant yet."
    ends = [_best_node(qdrant, collection, graph, embedder.encode_query(text)) for text in (from_concept, to_concept)]
    start, goal = ends
    if start is None or goal is None:
        return "One of the two concepts matches nothing in the knowledge graph."
    if start == goal:
        return f"Both phrases match the same concept: {graph.nodes[start].label}."

    # Concept-only paths read as reasoning; through a shared document only if none exists.
    paths = _shortest_paths(graph, start, goal, skip_documents=True) or _shortest_paths(
        graph, start, goal, skip_documents=False
    )
    trace.seeds |= {start, goal}
    if not paths:
        trace.nodes |= {start, goal}
        return f"No chain of relations connects {graph.nodes[start].label} and {graph.nodes[goal].label}."
    lines = []
    for path in paths:
        hops = []
        for edge in path:
            trace.edges.add(edge.key)
            trace.nodes |= {edge.source, edge.target}
            hops.append(_line(graph, edge))
        lines.append("\n".join(hops))
    return "<knowledge_graph>\n" + "\n\n".join(lines) + "\n</knowledge_graph>"


def _best_node(qdrant: QdrantClient, collection: str, graph: KnowledgeGraph, vector: list[float]) -> str | None:
    """The concept node nearest to `vector`."""
    hits = qdrant.query_points(
        collection_name=collection,
        query=vector,
        query_filter=Filter(must=[FieldCondition(key="element", match=MatchValue(value="node"))]),
        limit=3,
        with_payload=True,
    )
    for hit in hits.points:
        node_id = (hit.payload or {}).get("id")
        if node_id in graph.nodes and not graph.is_document(node_id):
            return node_id
    return None


def _shortest_paths(graph: KnowledgeGraph, start: str, goal: str, *, skip_documents: bool) -> list[list[KgEdge]]:
    """All shortest undirected paths (as edge lists), at most MAX_PATHS."""
    parents: dict[str, list[tuple[str, KgEdge]]] = {start: []}
    depth = {start: 0}
    frontier = deque([start])
    while frontier:
        node = frontier.popleft()
        if node == goal:
            break
        for neighbour, edge in graph.adjacency.get(node, []):
            if skip_documents and (graph.is_document(neighbour) or edge.kind == DOCUMENT_EDGE_KIND):
                continue
            if neighbour not in depth:
                depth[neighbour] = depth[node] + 1
                parents[neighbour] = [(node, edge)]
                frontier.append(neighbour)
            elif depth[neighbour] == depth[node] + 1:
                parents[neighbour].append((node, edge))
    if goal not in parents:
        return []

    paths: list[list[KgEdge]] = []

    def walk(node: str, suffix: list[KgEdge]) -> None:
        if len(paths) >= MAX_PATHS:
            return
        if node == start:
            paths.append(suffix)
            return
        for parent, edge in parents[node]:
            walk(parent, [edge, *suffix])

    walk(goal, [])
    return paths
