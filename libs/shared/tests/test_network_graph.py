"""Tests for the scenario network contract (ai_circus_shared.network_graph)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from ai_circus_shared import network_graph
from ai_circus_shared.network_graph import NetworkGraph, parse_graph, restrict_to_rows


def _doc(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "version": 1,
        "periods": ["2001-01", "2001-02"],
        "nodes": [
            {"id": "A", "kind": "row"},
            {"id": "B", "kind": "row", "group": "Community A"},
            {"id": "ctx-1", "kind": "context"},
            {"id": "ljm", "kind": "entity", "label": "LJM", "citation": "Report, p. 1"},
        ],
        "edges": [
            {"source": "A", "target": "B", "kind": "email", "weight": 5, "series": {"2001-01": 2, "2001-02": 3}},
            {"source": "B", "target": "ctx-1", "kind": "email", "weight": 3},
            {"source": "A", "target": "ljm", "kind": "role", "label": "General partner", "citation": "p. 2"},
        ],
    }
    doc.update(changes)
    return doc


def test_a_valid_graph_round_trips() -> None:
    graph = NetworkGraph.model_validate(_doc())
    assert [n.kind for n in graph.nodes] == ["row", "row", "context", "entity"]
    assert graph.edges[0].series == {"2001-01": 2.0, "2001-02": 3.0}
    assert NetworkGraph.model_validate_json(graph.model_dump_json()) == graph


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"nodes": [{"id": "A", "kind": "row"}, {"id": "A", "kind": "row"}], "edges": []}, "unique"),
        ({"edges": [{"source": "A", "target": "nobody", "kind": "email"}]}, "unknown node"),
        ({"edges": [{"source": "A", "target": "A", "kind": "email"}]}, "self-loop"),
        ({"edges": [{"source": "A", "target": "B", "kind": "email", "series": {"1999-01": 1}}]}, "unknown periods"),
        ({"edges": [{"source": "A", "target": "B", "kind": "email", "series": {"2001-01": -1}}]}, "greater than"),
        ({"periods": ["2001-02", "2001-01"]}, "strictly increase"),
        ({"periods": ["January"]}, "pattern"),
        ({"edges": [{"source": "A", "target": "B", "kind": "Email"}]}, "pattern"),
        ({"nodes": [{"id": "e", "kind": "entity"}], "edges": []}, "need a label"),
        ({"nodes": [{"id": "A", "kind": "person"}], "edges": []}, "row"),
    ],
)
def test_inconsistent_graphs_are_rejected(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        NetworkGraph.model_validate(_doc(**changes))


def test_duplicate_edges_of_one_kind_are_rejected_but_other_kinds_are_fine() -> None:
    twice = {"source": "A", "target": "B", "kind": "email"}
    with pytest.raises(ValidationError, match="unique per"):
        NetworkGraph.model_validate(_doc(edges=[twice, twice]))
    NetworkGraph.model_validate(_doc(edges=[twice, {**twice, "kind": "role"}, {**twice, "source": "B", "target": "A"}]))


def test_size_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [{"id": str(i), "kind": "row"} for i in range(network_graph.MAX_GRAPH_NODES + 1)]
    with pytest.raises(ValidationError, match="at most"):
        NetworkGraph.model_validate({"nodes": nodes})
    monkeypatch.setattr(network_graph, "MAX_SERIES_POINTS", 1)
    with pytest.raises(ValidationError, match="series exceed"):
        NetworkGraph.model_validate(_doc())


def test_parse_graph_refuses_oversized_documents_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    data = json.dumps(_doc()).encode()
    assert len(parse_graph(data).nodes) == 4
    monkeypatch.setattr(network_graph, "MAX_GRAPH_BYTES", len(data) - 1)
    with pytest.raises(ValueError, match="byte limit"):
        parse_graph(data)


def test_restrict_to_rows_drops_lost_rows_their_edges_and_orphaned_context() -> None:
    graph = NetworkGraph.model_validate(_doc())
    restricted = restrict_to_rows(graph, ["A"])  # B was dropped by the ETL
    assert [n.id for n in restricted.nodes] == ["A", "ljm"]  # ctx-1 only linked to B; the entity stays
    assert [(e.source, e.target) for e in restricted.edges] == [("A", "ljm")]
    assert restrict_to_rows(graph, ["A", "B"]) == graph  # ids compared as strings, nothing lost


def test_restrict_to_rows_compares_ids_as_strings() -> None:
    graph = NetworkGraph.model_validate(
        {
            "nodes": [{"id": "1", "kind": "row"}, {"id": "2", "kind": "row"}],
            "edges": [{"source": "1", "target": "2", "kind": "x"}],
        }
    )
    assert len(restrict_to_rows(graph, [1, 2]).edges) == 1


def test_periods_may_be_hourly() -> None:
    doc = _doc(
        periods=["2022-09-01T00", "2022-09-01T01", "2022-09-02"],
        edges=[{"source": "A", "target": "B", "kind": "ach", "series": {"2022-09-01T01": 1250.5}}],
    )
    assert NetworkGraph.model_validate(doc).edges[0].series == {"2022-09-01T01": 1250.5}
    with pytest.raises(ValidationError, match="pattern"):
        NetworkGraph.model_validate(_doc(periods=["2022-09-01T24:00"]))
    with pytest.raises(ValidationError, match="strictly increase"):
        NetworkGraph.model_validate(_doc(periods=["2022-09-01T05", "2022-09-01T05"]))
