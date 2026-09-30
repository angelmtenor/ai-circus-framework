"""Tests for GraphRAG over a tenant's knowledge graph: loading, seed linking,
Personalized PageRank, the capped context, shortest paths, the tools and the endpoint.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from ai_circus_shared.auth import Identity
from ai_circus_shared.scenario_schema import (
    ChatConfig,
    DocumentChunking,
    DocumentEmbedding,
    DocumentsConfig,
    KnowledgeGraphConfig,
    VectorStoreConfig,
)
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from rag_agent.api import _graph_cache, _qdrant, _scenario_definition, knowledge_graph_endpoint, router
from rag_agent.core.agent import build_agui_agent, build_graph_tools
from rag_agent.core.graph_retrieval import (
    GraphTrace,
    KnowledgeGraphCache,
    graph_path,
    graph_search,
    load_graph,
    personalized_pagerank,
    within_hops,
)
from rag_agent.core.identity import resolve_identity

VECTOR_STORE = VectorStoreConfig(backend="qdrant", collection_prefix="amlr", top_k=3)
KG_COLLECTION = "amlr_kg__org-1"
DOC_COLLECTION = "amlr__org-1"


def _documents(max_triples: int = 30) -> DocumentsConfig:
    # model_construct: a cap below the schema's floor, so the toy graph can overflow it.
    config = KnowledgeGraphConfig.model_construct(seed_file="kg.json", max_triples=max_triples, max_passages=1, hops=2)
    return DocumentsConfig(
        bucket="b",
        raw_prefix="raw/",
        seed_prefix="sample_docs",
        chunking=DocumentChunking(strategy="recursive_character", chunk_size=800, chunk_overlap=120),
        embedding=DocumentEmbedding(model="m"),
        knowledge_graph=config,
    )


def _node(node_id: str, label: str, node_type: str, citation: str = "") -> SimpleNamespace:
    payload = {"element": "node", "id": node_id, "label": label, "type": node_type, "description": label}
    if citation:
        payload["citation"] = citation
    return SimpleNamespace(payload=payload)


def _edge(source: str, kind: str, target: str, citation: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        payload={"element": "edge", "source": source, "kind": kind, "target": target, "citation": citation}
    )


# obliged entity -> report suspicion -> FIU, report <- suspicion trigger; a cash-limit
# branch two hops from the reporting one; one document node per article.
KG_POINTS = [
    _node("oe", "Obliged entity", "ObligedEntity"),
    _node("report", "Report suspicion to the FIU", "Obligation"),
    _node("fiu", "FIU", "Authority"),
    _node("suspicion", "Suspicion of criminal proceeds", "Trigger"),
    _node("cash", "Accept cash payments", "Obligation"),
    _node("limit", "EUR 10 000", "Limit"),
    _node("art69", "Art. 69 — Reporting of suspicions", "Article", "amlr-art-069.md"),
    _node("art80", "Art. 80 — Cash limit", "Article", "amlr-art-080.md"),
    _edge("oe", "must_perform", "report", "AMLR Art. 69(1)"),
    _edge("report", "reports_to", "fiu", "AMLR Art. 69(1)"),
    _edge("report", "triggered_by", "suspicion", "AMLR Art. 69(1)"),
    _edge("oe", "must_not", "cash", "AMLR Art. 80(1)"),
    _edge("cash", "has_limit", "limit", "AMLR Art. 80(1)"),
    _edge("report", "cited_in", "art69"),
    _edge("fiu", "cited_in", "art69"),
    _edge("suspicion", "cited_in", "art69"),
    _edge("cash", "cited_in", "art80"),
    _edge("limit", "cited_in", "art80"),
    _edge("report", "similar_to", "cash"),  # inferred synonymy: steers PageRank, never a fact line
    _edge("ghost", "must_perform", "report"),  # dangling: dropped on load
]


class FakeQdrant:
    """In-memory stand-in: one knowledge-graph collection and one document collection."""

    def __init__(self, kg_hits: list[tuple[SimpleNamespace, float]], *, has_kg: bool = True) -> None:
        """Configure the knowledge-graph search hits and whether its collection exists."""
        self.kg_hits = kg_hits
        self.has_kg = has_kg
        self.scrolls = 0
        self.queries: list[dict[str, Any]] = []

    def collection_exists(self, name: str) -> bool:
        """Only this tenant's two collections exist."""
        return name == DOC_COLLECTION or (self.has_kg and name == KG_COLLECTION)

    def scroll(self, collection: str, **_kwargs: object) -> tuple[list[SimpleNamespace], None]:
        """Return the whole toy graph in one page."""
        assert collection == KG_COLLECTION
        self.scrolls += 1
        return KG_POINTS, None

    def query_points(self, collection_name: str, **kwargs: Any) -> SimpleNamespace:
        """Record the query; answer document queries from the allowed sources, graph ones from the hits."""
        self.queries.append({"collection_name": collection_name, **kwargs})
        query_filter = kwargs.get("query_filter")
        if collection_name == DOC_COLLECTION:
            allowed = query_filter.must[0].match.any
            points = [
                SimpleNamespace(payload={"source": source, "text": f"text of {source}"}, score=0.8)
                for source in allowed
            ]
            return SimpleNamespace(points=points[: kwargs["limit"]])
        hits = [SimpleNamespace(payload=p.payload, score=s) for p, s in self.kg_hits]
        if query_filter is not None:  # element == "node"
            hits = [h for h in hits if h.payload["element"] == "node"]
        return SimpleNamespace(points=hits[: kwargs["limit"]])


class FakeEmbedder:
    """Deterministic stand-in for an EmbeddingProvider."""

    def encode_query(self, text: str) -> list[float]:
        """Return a fixed vector regardless of input."""
        return [0.1, 0.2]


def _by_id(node_id: str) -> SimpleNamespace:
    return next(p for p in KG_POINTS if p.payload.get("id") == node_id)


def test_load_graph_reads_nodes_and_edges_and_drops_dangling_edges() -> None:
    graph = load_graph(FakeQdrant([]), KG_COLLECTION)

    assert graph is not None
    assert set(graph.nodes) == {"oe", "report", "fiu", "suspicion", "cash", "limit", "art69", "art80"}
    assert all(e.source != "ghost" for e in graph.edges)
    assert graph.documents == {"art69", "art80"}


def test_load_graph_is_none_without_a_collection() -> None:
    assert load_graph(FakeQdrant([], has_kg=False), KG_COLLECTION) is None


def test_within_hops_bounds_the_radius() -> None:
    graph = load_graph(FakeQdrant([]), KG_COLLECTION)
    assert graph is not None

    assert within_hops(graph, {"fiu"}, 1) == {"fiu", "report", "art69"}
    assert "oe" in within_hops(graph, {"fiu"}, 2)
    assert "limit" not in within_hops(graph, {"fiu"}, 2)


def test_personalized_pagerank_concentrates_mass_near_the_seeds() -> None:
    graph = load_graph(FakeQdrant([]), KG_COLLECTION)
    assert graph is not None
    allowed = set(graph.nodes)

    rank = personalized_pagerank(graph, {"fiu": 1.0}, allowed)

    assert sum(rank.values()) == pytest.approx(1.0)
    assert rank["fiu"] == max(rank.values())
    assert rank["report"] > rank["limit"]


def test_graph_search_returns_capped_cited_relations_passages_and_a_trace() -> None:
    qdrant = FakeQdrant([(_by_id("fiu"), 0.9), (KG_POINTS[9], 0.7)])  # FIU node + report->FIU relation
    trace = GraphTrace()

    content, sources = graph_search(
        qdrant, FakeEmbedder(), KnowledgeGraphCache(), _documents(max_triples=2), VECTOR_STORE, "org-1", "q", trace
    )

    lines = content.split("<knowledge_graph>\n")[1].split("\n</knowledge_graph>")[0].splitlines()
    # The relation between both seeds leads, the cap holds, every line cites its article.
    assert lines[0] == "Report suspicion to the FIU --reports_to--> FIU [AMLR Art. 69(1)]"
    assert len(lines) == 2
    assert '<retrieved_document source="raw/amlr-art-069.md">' in content
    assert sources == [{"source": "raw/amlr-art-069.md", "score": 0.8}]
    assert {"fiu", "report"} <= trace.seeds
    assert ("report", "reports_to", "fiu") in trace.edges
    assert "art69" in trace.nodes
    # Tenant-scoped collections only.
    assert {q["collection_name"] for q in qdrant.queries} == {KG_COLLECTION, DOC_COLLECTION}


def test_graph_search_without_a_graph_or_a_match_says_so() -> None:
    trace = GraphTrace()
    no_graph = FakeQdrant([], has_kg=False)
    content, sources = graph_search(
        no_graph, FakeEmbedder(), KnowledgeGraphCache(), _documents(), VECTOR_STORE, "org-1", "q", trace
    )
    assert "not available" in content and sources == []

    content, _ = graph_search(
        FakeQdrant([]), FakeEmbedder(), KnowledgeGraphCache(), _documents(), VECTOR_STORE, "org-1", "q", trace
    )
    assert "No concept" in content
    assert trace.as_event() == {"seeds": [], "nodes": [], "edges": []}


def test_graph_path_chains_relations_between_two_concepts_without_crossing_documents() -> None:
    qdrant = FakeQdrant([(_by_id("oe"), 0.9), (_by_id("fiu"), 0.8)])

    class TwoEmbedder:
        def encode_query(self, text: str) -> list[float]:
            qdrant.kg_hits = [(_by_id("oe" if text == "bank" else "fiu"), 0.9)]
            return [0.1]

    trace = GraphTrace()
    content = graph_path(qdrant, TwoEmbedder(), KnowledgeGraphCache(), VECTOR_STORE, "org-1", "bank", "FIU", trace)

    assert content.splitlines()[1:3] == [
        "Obliged entity --must_perform--> Report suspicion to the FIU [AMLR Art. 69(1)]",
        "Report suspicion to the FIU --reports_to--> FIU [AMLR Art. 69(1)]",
    ]
    assert trace.seeds == {"oe", "fiu"}
    assert "art69" not in trace.nodes


def test_graph_path_same_concept_and_missing_graph() -> None:
    qdrant = FakeQdrant([(_by_id("fiu"), 0.9)])
    trace = GraphTrace()
    content = graph_path(qdrant, FakeEmbedder(), KnowledgeGraphCache(), VECTOR_STORE, "org-1", "a", "b", trace)
    assert "same concept" in content

    missing = FakeQdrant([], has_kg=False)
    assert "not available" in graph_path(
        missing, FakeEmbedder(), KnowledgeGraphCache(), VECTOR_STORE, "org-1", "a", "b", trace
    )


def test_knowledge_graph_cache_loads_once_and_stays_bounded() -> None:
    qdrant = FakeQdrant([])
    cache = KnowledgeGraphCache(max_entries=1)

    first = cache.get(qdrant, KG_COLLECTION)
    assert cache.get(qdrant, KG_COLLECTION) is first
    assert qdrant.scrolls == 1

    cache.get(FakeQdrant([], has_kg=False), "other")
    assert list(cache._data) == ["other"]


def test_build_graph_tools_capture_sources_and_trace_and_compile_into_the_agent() -> None:
    from tests.test_core_agent import FakeChatModel

    qdrant = FakeQdrant([(_by_id("fiu"), 0.9)])
    tools, captured, trace = build_graph_tools(
        qdrant, FakeEmbedder(), KnowledgeGraphCache(), _documents(), VECTOR_STORE, "org-1"
    )

    assert [t.name for t in tools] == ["graph_search", "graph_path"]
    tools[0].invoke({"question": "Who receives reports?"})
    assert captured["sources"] == [{"source": "raw/amlr-art-069.md", "score": 0.8}]
    assert "fiu" in trace.seeds
    assert build_agui_agent(FakeChatModel(), tools, "AML law", knowledge_graph=True) is not None


def _definition(documents: DocumentsConfig | None) -> SimpleNamespace:
    return SimpleNamespace(slug="amlr", documents=documents, vector_store=VECTOR_STORE, chat=ChatConfig(context="x"))


def test_knowledge_graph_endpoint_serves_the_callers_tenant_graph() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="org-1", roles=frozenset())
    app.dependency_overrides[_scenario_definition] = lambda: _definition(_documents())
    app.dependency_overrides[_qdrant] = lambda: FakeQdrant([])
    app.dependency_overrides[_graph_cache] = KnowledgeGraphCache

    response = TestClient(app).get("/knowledge-graph/amlr")

    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) == 8
    assert {"source": "report", "target": "fiu", "kind": "reports_to", "label": "reports to"}.items() <= next(
        e for e in body["edges"] if e["kind"] == "reports_to"
    ).items()


def test_knowledge_graph_endpoint_404s_without_a_graph() -> None:
    identity = Identity(subject="u", org_id="org-1", roles=frozenset())
    with pytest.raises(HTTPException) as no_config:
        knowledge_graph_endpoint(identity, _definition(None), FakeQdrant([]), KnowledgeGraphCache())
    assert no_config.value.status_code == 404

    with pytest.raises(HTTPException) as not_indexed:
        knowledge_graph_endpoint(
            identity, _definition(_documents()), FakeQdrant([], has_kg=False), KnowledgeGraphCache()
        )
    assert not_indexed.value.status_code == 404


def test_synonym_edges_spread_relevance_but_are_never_returned_as_facts() -> None:
    qdrant = FakeQdrant([(_by_id("report"), 0.9)])
    trace = GraphTrace()

    content, _ = graph_search(
        qdrant, FakeEmbedder(), KnowledgeGraphCache(), _documents(), VECTOR_STORE, "org-1", "q", trace
    )

    assert "similar_to" not in content
    assert ("report", "similar_to", "cash") not in trace.edges
    graph = load_graph(FakeQdrant([]), KG_COLLECTION)
    assert graph is not None
    assert "cash" in within_hops(graph, {"report"}, 1)  # the synonym is one hop away


def test_each_key_concept_links_its_own_seeds_up_to_the_cap() -> None:
    qdrant = FakeQdrant([(_by_id("fiu"), 0.9)])
    concepts = ["report suspicion", "FIU", "deadline", "limit", "one too many"]

    graph_search(
        qdrant, FakeEmbedder(), KnowledgeGraphCache(), _documents(), VECTOR_STORE, "org-1", "q", GraphTrace(), concepts
    )

    limits = [q["limit"] for q in qdrant.queries if q["collection_name"] == KG_COLLECTION]
    assert limits == [8, 5, 5, 5, 5]  # the question, then MAX_CONCEPTS concepts


def test_seed_weight_is_divided_by_how_many_documents_a_concept_appears_in() -> None:
    from rag_agent.core.graph_retrieval import link_seeds

    graph = load_graph(FakeQdrant([]), KG_COLLECTION)
    assert graph is not None
    qdrant = FakeQdrant([(_by_id("oe"), 0.8), (_by_id("fiu"), 0.8)])

    seeds = link_seeds(qdrant, KG_COLLECTION, graph, [([0.1], 8)])

    assert graph.document_count("fiu") == 1 and graph.document_count("oe") == 0
    assert seeds == {"oe": 0.8, "fiu": 0.8}
    graph.adjacency["fiu"].append(("art80", next(e for e in graph.edges if e.kind == "cited_in")))
    assert link_seeds(qdrant, KG_COLLECTION, graph, [([0.1], 8)])["fiu"] == pytest.approx(0.8 / 2**0.5)


def test_graph_search_is_capped_per_run() -> None:
    """Every extra search is prompt paid for again: after the cap the tool refuses cheaply."""
    from rag_agent.core.agent import MAX_GRAPH_SEARCHES_PER_RUN

    qdrant = FakeQdrant([(_by_id("fiu"), 0.9)])
    tools, _captured, _trace = build_graph_tools(
        qdrant, FakeEmbedder(), KnowledgeGraphCache(), _documents(), VECTOR_STORE, "org-1"
    )
    for _ in range(MAX_GRAPH_SEARCHES_PER_RUN):
        assert "<knowledge_graph>" in tools[0].invoke({"question": "q"})
    assert tools[0].invoke({"question": "q"}).startswith("Search limit reached")
