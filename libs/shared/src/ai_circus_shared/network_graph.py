"""The network a `tabular_ml` scenario can ship next to its rows (`dataset.graph`).

Some scenarios are about *who is connected to whom* as much as about each row: people
e-mailing each other, accounts paying each other, companies sharing directors. The
model still scores one row at a time (network position arrives as ordinary numeric
features, computed offline), but the UI needs the network itself to draw it. This is
that file's contract — one JSON document per scenario, written by the scenario's
prepare script to `sample_data/`, bootstrapped into the tenant's bucket by
etl-tabular (restricted to the rows that survived cleaning, at
`tabular_ml.GRAPH_KEY`) and served, tenant-scoped, by prediction's `GET /graph/{slug}`.

Three kinds of node:
- `row`: a dataset row (its `id` is the row's `index_col` value) — scored by the model.
- `entity`: something that is not a row but matters to the story (a company, a
  partnership, an auditor) — curated, usually with a `citation`.
- `context`: an unscored node from the same data that connects rows (e.g. another
  employee who relays e-mails) — anonymised by the prepare script.

Edges are directed (`source` → `target`) and typed by `kind`. An edge may carry a
`series`: its weight per period (e.g. e-mails per month, or dollars per hour), keyed by
`periods`, so the UI can replay the network over time.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Hard bounds: the whole document is served in one response and drawn in a browser.
MAX_GRAPH_NODES = 2000
MAX_GRAPH_EDGES = 20000
MAX_GRAPH_PERIODS = 240
MAX_SERIES_POINTS = 400_000
MAX_GRAPH_BYTES = 8_000_000

NodeKind = Literal["row", "entity", "context"]
_PERIOD = r"^\d{4}(-\d{2}(-\d{2}(T\d{2})?)?)?$"  # "2001", "2001-10", "2001-10-16" or "2022-09-03T14"


class GraphNode(BaseModel):
    """One node. Only `id` and `kind` are required; `label` defaults to the id in the UI
    (rows usually take their name from the dataset's display columns instead)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    kind: NodeKind
    label: str | None = Field(default=None, max_length=200)
    type: str | None = Field(default=None, max_length=80)  # e.g. "Special-purpose entity"
    description: str | None = Field(default=None, max_length=2000)
    citation: str | None = Field(default=None, max_length=500)
    group: str | None = Field(default=None, max_length=80)  # e.g. a detected community


class GraphEdge(BaseModel):
    """One directed, typed edge — e.g. `kind: email` weighted by messages, or
    `kind: role` labelled "general partner of" with a `citation`."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    weight: float = Field(default=1.0, ge=0.0)
    label: str | None = Field(default=None, max_length=200)
    citation: str | None = Field(default=None, max_length=500)
    # A verbatim quote from the cited source backing this edge (knowledge graphs
    # extracted from text, e.g. `aml_regulation_kg` — see its prepare script).
    evidence: str | None = Field(default=None, max_length=1000)
    series: dict[str, Annotated[float, Field(ge=0.0)]] = {}  # period -> weight in that period (sparse)


class NetworkGraph(BaseModel):
    """A scenario's network (see the module docstring)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    # Increasing time buckets edge `series` are keyed by, e.g. "1999-01" … "2002-06".
    periods: list[Annotated[str, Field(pattern=_PERIOD)]] = Field(default=[], max_length=MAX_GRAPH_PERIODS)
    nodes: list[GraphNode] = Field(max_length=MAX_GRAPH_NODES)
    edges: list[GraphEdge] = Field(default=[], max_length=MAX_GRAPH_EDGES)

    @model_validator(mode="after")
    def _references_are_consistent(self) -> NetworkGraph:
        """Unique node ids; strictly increasing periods; labelled entities; every edge
        joins two known, distinct nodes, once per kind; every series key is a declared
        period; the series stay within MAX_SERIES_POINTS."""
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("graph node ids must be unique.")
        if any(a >= b for a, b in zip(self.periods, self.periods[1:], strict=False)):
            raise ValueError("graph periods must strictly increase.")
        unlabelled = [node.id for node in self.nodes if node.kind == "entity" and not node.label]
        if unlabelled:
            raise ValueError(f"graph entity nodes need a label: {unlabelled[:5]}.")
        if sum(len(edge.series) for edge in self.edges) > MAX_SERIES_POINTS:
            raise ValueError(f"graph edge series exceed {MAX_SERIES_POINTS} points in total.")
        keys = [(edge.source, edge.target, edge.kind) for edge in self.edges]
        if len(set(keys)) != len(keys):
            raise ValueError("graph edges must be unique per (source, target, kind).")
        known, periods = set(ids), set(self.periods)
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError(f"graph edge {edge.source!r} -> {edge.target!r} references an unknown node.")
            if edge.source == edge.target:
                raise ValueError(f"graph edge {edge.source!r} -> itself is a self-loop.")
            unknown = set(edge.series) - periods
            if unknown:
                raise ValueError(
                    f"graph edge {edge.source!r} -> {edge.target!r} has unknown periods {sorted(unknown)}."
                )
        return self


def parse_graph(data: bytes) -> NetworkGraph:
    """Validate a graph document, refusing anything over MAX_GRAPH_BYTES before parsing."""
    if len(data) > MAX_GRAPH_BYTES:
        raise ValueError(f"graph document is {len(data):,} bytes, over the {MAX_GRAPH_BYTES:,}-byte limit.")
    return NetworkGraph.model_validate_json(data)


def restrict_to_rows(graph: NetworkGraph, row_ids: Iterable[object]) -> NetworkGraph:
    """The graph as it applies to one cleaned dataset: `row` nodes whose id is not a
    row any more (etl-tabular dropped it) disappear with their edges, and so does any
    `context` node left with no edge at all (it only ever existed to connect rows).
    Entities stay — they are curated content, relevant even when unconnected.
    """
    rows = {str(row_id) for row_id in row_ids}
    kept = {node.id for node in graph.nodes if node.kind != "row" or node.id in rows}
    edges = [edge for edge in graph.edges if edge.source in kept and edge.target in kept]
    connected = {edge.source for edge in edges} | {edge.target for edge in edges}
    nodes = [node for node in graph.nodes if node.id in kept and (node.kind != "context" or node.id in connected)]
    return graph.model_copy(update={"nodes": nodes, "edges": edges})
