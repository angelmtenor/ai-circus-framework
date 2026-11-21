"""
- Title:    The architecture catalog: every component, its layer, what it talks to, and each scenario's subset
- Author:   Angel Martinez-Tenor

`architecture_catalog.yaml` (next to this module) is the one description of the platform's architecture. This module
validates it and derives everything else from it, so nothing is listed by hand twice:

- `scenario_architecture(definition)`: the components a scenario uses and why, derived from what its scenario.yaml
  already declares (kind, documents, vector store, challenger, rubric, analysis toolset, MCP door, extra models) —
  platform-registry stores it with the scenario and the app draws it on the Overview tab;
- `export(catalog, adr_files)`: the JSON copy the app's Architecture page reads (ui-react/src/generated/);
- `render_svg(catalog)` and `render_markdown(catalog, adr_files)`: docs/architecture/architecture.svg and
  components.md, written by scripts/generate_architecture.py; a test fails when a committed copy drifted.

Pure Python and content-free: names, roles and links, never data.
"""

from __future__ import annotations

from functools import lru_cache
from html import escape
from pathlib import Path
from textwrap import wrap
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CATALOG_PATH = Path(__file__).with_name("architecture_catalog.yaml")
LayerId = Literal["gateway", "consumption", "data", "infra", "observability"]


class Layer(BaseModel):
    """One layer of the platform."""

    model_config = ConfigDict(frozen=True)

    id: LayerId
    title: str
    text: str


class Link(BaseModel):
    """A component talks to another, for a reason."""

    model_config = ConfigDict(frozen=True)

    to: str
    label: str


class Component(BaseModel):
    """One component: a service, a store, a job, a UI, a gateway, a monitor or something outside the cluster."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    layer: LayerId
    kind: Literal["platform", "model", "external", "store", "job", "service", "ui", "gateway", "monitor"]
    tech: str
    role: str
    profile: Literal["core", "optional", "external"]
    enable: str | None = None
    source: str | None = None
    workloads: list[str] = []
    adr: list[int] = []
    talks_to: list[Link] = []


class FlowStep(BaseModel):
    """One hop of a request."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    source: str = Field(alias="from")
    to: str
    label: str


class Flow(BaseModel):
    """How a request travels through the components, step by step."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    text: str
    steps: list[FlowStep] = Field(min_length=2)

    def components(self) -> list[str]:
        """Every component the flow touches, in order of first appearance."""
        seen: list[str] = []
        for step in self.steps:
            for cid in (step.source, step.to):
                if cid not in seen:
                    seen.append(cid)
        return seen


class Catalog(BaseModel):
    """The whole catalog, checked: unique ids, known layers, and every link and flow step between real components."""

    model_config = ConfigDict(frozen=True)

    layers: list[Layer]
    components: list[Component]
    flows: list[Flow]

    @model_validator(mode="after")
    def _references_are_real(self) -> Catalog:
        ids = [c.id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate component ids: {sorted({i for i in ids if ids.count(i) > 1})}.")
        if {layer.id for layer in self.layers} != set(get_args(LayerId)):
            raise ValueError("Every layer must be described once.")
        known = set(ids)
        for component in self.components:
            for link in component.talks_to:
                if link.to not in known:
                    raise ValueError(f"{component.id} talks to unknown component {link.to!r}.")
        for flow in self.flows:
            for cid in flow.components():
                if cid not in known:
                    raise ValueError(f"Flow {flow.id!r} names unknown component {cid!r}.")
        workloads = [w for c in self.components for w in c.workloads]
        if len(workloads) != len(set(workloads)):
            raise ValueError("A workload belongs to one component only.")
        return self

    def component(self, cid: str) -> Component:
        """The component with this id (KeyError if none)."""
        for component in self.components:
            if component.id == cid:
                return component
        raise KeyError(cid)

    def by_layer(self, layer: str) -> list[Component]:
        """The layer's components, in catalog order."""
        return [c for c in self.components if c.layer == layer]


@lru_cache(maxsize=1)
def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    """The validated catalog (cached: it ships with the package and never changes at run time)."""
    return Catalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# ---- a scenario's subset ----------------------------------------------------------------------------------------


def scenario_architecture(definition: Any, catalog: Catalog | None = None) -> dict[str, Any]:
    """The components a scenario uses and why, and the request flows that run entirely inside them.

    Derived only from what the scenario.yaml declares (`definition` is a `ScenarioDefinition`; typed loosely so the
    schema module and this one stay independent), so a new scenario gets its view with no list to maintain.
    """
    catalog = catalog or load_catalog()
    reasons: dict[str, str] = {}

    def use(cid: str, why: str) -> None:
        reasons[cid] = f"{reasons[cid]} {why}" if cid in reasons and why not in reasons[cid] else why

    use("k3s", "Runs every pod this scenario needs.")
    use("traefik", "Routes the browser's calls to each service by hostname.")
    use("keycloak", "Signs the user in; the tenant is their organization.")
    use("platform-registry", "Serves this scenario and checks the tenant's entitlement on every call.")
    use("postgres", "Holds tenants, entitlements and chat history.")
    use("ui-react", "Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.")
    kind = definition.kind
    chat = kind != "deep_learning"
    if chat:
        use("llm-gateway", "Every model call of the chat goes through it, with the tenant's limits and budget.")
        use("llm-providers", "The active LLM answers the chat.")
        use("langfuse", "A trace per LLM call, by tenant and scenario.")
        use("valkey", "The gateway's rate-limit and budget counters.")
    if kind == "tabular_ml":
        models = 1 + len(getattr(definition, "extra_models", []) or [])
        use("seaweedfs", "The dataset and the trained model, under the tenant's prefix.")
        use(
            "etl-tabular",
            "Cleans the dataset" + (" and applies its business rules." if _has_rules(definition) else "."),
        )
        use(
            "training",
            f"Trains {f'each of its {models} models' if models > 1 else 'the model'} and picks the winner "
            "by its metric.",
        )
        use("mlflow", "Records every training run: candidates, scores, the selected model.")
        use(
            "prediction",
            f"Serves {f'the {models} models' if models > 1 else 'the model'} with SHAP explanations for every tab.",
        )
        use("assistant", "The chat: answers from the model's tools, never from memory.")
        if getattr(definition, "analysis_toolset", None):
            use(
                "assistant",
                f"Adds the {definition.analysis_toolset.replace('_', ' ')} tools and the routes of its control room.",
            )
        if getattr(getattr(definition, "model", None), "text_challenger", None) is not None:
            use("llm-gateway", "Embeds the text for the challenger model (local-embed).")
            use("training", "Trains the text challenger on sentence embeddings.")
        if getattr(definition, "rubric_check", None) is not None:
            use("llm-gateway", "The LLM reviewer reads a record against the rubric.")
    elif kind == "conversational_rag":
        use("seaweedfs", "The source documents.")
        use("rag-agent", "Answers from the retrieved passages and cites each one.")
    elif kind == "assisted_form":
        use("form-agent", "Fills the form through conversation, validates it and prints the PDF.")
        use("seaweedfs", "Each submission, by tenant and user.")
    elif kind == "deep_learning":
        use("seaweedfs", "The images or texts and the exported ONNX model.")
        use("dl-training", "Fine-tunes the model and exports it to ONNX.")
        use("dl-inference", "Scores every input with ONNX Runtime.")
        use("gpu", "Training runs on the host GPU when there is one.")
    elif kind == "data_product":
        use("seaweedfs", "The landing files and the Iceberg lake.")
        use("kafka", "The events of its sources, a topic per contract and tenant.")
        use("data-ingest", "Validates every record against its contract into bronze, quarantines what fails.")
        use("data-transform", "Builds silver and gold with tested dbt models.")
        use("data-api", "The governed door: metrics, records and SQL filtered by tenant and role.")
        use("clickhouse", "The analytical marts on the analytics tier.")
        use("dagster", "Orchestrates the transformations on the analytics tier.")
        use("superset", "Its dashboards, imported from the scenario's YAML.")
        use("openmetadata", "Its datasets' owners and lineage on the governance tier.")
        use("assistant", "The chat: answers from the governed data tools.")
    if getattr(definition, "documents", None) is not None or getattr(definition, "vector_store", None) is not None:
        use("etl-vectorize", "Chunks and embeds the documents into the tenant's collection.")
        use("qdrant", "The tenant's collection of passages.")
        use("llm-gateway", "Embeds questions and passages (local-embed).")
    mcp = getattr(definition, "mcp", None)
    if mcp is not None and getattr(mcp, "expose", None):
        if kind == "tabular_ml":
            use("assistant", "Serves the model's read-only MCP door, with the caller's own token.")
        use("llm-gateway", "The MCP gateway routes its read-only door to MCP clients.")
        use("chat-ui", "Optional: the same tools from LibreChat or Open WebUI, with the user's own token.")
    if getattr(definition, "governance", None) is not None:
        use("platform-registry", "Lists it in the AI-BOM with its declared governance block.")
    order = [c.id for c in catalog.components]
    used = [cid for cid in order if cid in reasons]
    flows = [f.id for f in catalog.flows if set(f.components()) <= set(used)]
    return {"components": used, "reasons": {cid: reasons[cid] for cid in used}, "flows": flows}


def _has_rules(definition: Any) -> bool:
    dataset = getattr(definition, "dataset", None)
    return dataset is not None and getattr(dataset, "business_rules", None) is not None


# ---- exports: the app's JSON, the docs' SVG and markdown --------------------------------------------------------


def export(catalog: Catalog, adr_files: dict[int, str]) -> dict[str, Any]:
    """The JSON the app's Architecture page reads: the catalog, with each ADR number resolved to its file name."""
    data = catalog.model_dump(by_alias=True)
    data["adr_files"] = {
        str(n): adr_files[n] for n in sorted({a for c in catalog.components for a in c.adr}) if n in adr_files
    }
    return data


_LAYER_COLORS = {
    "gateway": "#aa3bff",
    "consumption": "#33c7ff",
    "data": "#22d3c8",
    "infra": "#ffcf4d",
    "observability": "#ff8fd6",
}
_STACK = ["gateway", "consumption", "data", "infra"]
_FONT = "Inter, 'Segoe UI', Arial, Helvetica, sans-serif"


def render_svg(catalog: Catalog) -> str:
    """The layered map: people and agents at the top, then gateways, consumption, data and infrastructure, with
    observability alongside. Optional components have a dashed border. Deterministic (the docs commit it)."""
    width, left, obs_w, gap = 1640, 40, 300, 24
    main_w = width - left * 2 - obs_w - gap
    cols, box_w, box_h, box_gap = 5, (main_w - 40 - 4 * 14) // 5, 88, 14
    out: list[str] = []
    y = 120

    def box(x: float, y0: float, w: float, c: Component, color: str) -> None:
        dash = ' stroke-dasharray="6 4"' if c.profile != "core" else ""
        out.append(
            f'<rect x="{x}" y="{y0}" width="{w}" height="{box_h}" rx="10" fill="#0b1224" stroke="{color}" '
            f'stroke-opacity="0.75" stroke-width="1.4"{dash}/>'
        )
        out.append(
            f'<text x="{x + 12}" y="{y0 + 24}" font-size="15" font-weight="700" fill="#f3fbff">{escape(c.name)}</text>'
        )
        for i, line in enumerate(wrap(c.tech, 34)[:2]):
            out.append(f'<text x="{x + 12}" y="{y0 + 44 + i * 16}" font-size="12" fill="#7fa3c9">{escape(line)}</text>')
        if c.profile != "core":
            badge = "OPTIONAL" if c.profile == "optional" else "OUTSIDE THE CLUSTER"
            out.append(
                f'<text x="{x + w - 10}" y="{y0 + box_h - 9}" font-size="10" fill="{color}" text-anchor="end" '
                f'letter-spacing="0.8">{badge}</text>'
            )

    band_tops: dict[str, tuple[float, float]] = {}
    for layer_id in _STACK:
        layer = next(layer for layer in catalog.layers if layer.id == layer_id)
        items = catalog.by_layer(layer_id)
        rows = (len(items) + cols - 1) // cols
        height = 56 + rows * (box_h + box_gap) + 6
        color = _LAYER_COLORS[layer_id]
        out.append(
            f'<rect x="{left}" y="{y}" width="{main_w}" height="{height}" rx="14" fill="{color}" fill-opacity="0.05" '
            f'stroke="{color}" stroke-opacity="0.45"/>'
        )
        out.append(
            f'<text x="{left + 20}" y="{y + 30}" font-size="17" font-weight="700" fill="{color}" '
            f'letter-spacing="1.5">{escape(layer.title.upper())}</text>'
        )
        for i, c in enumerate(items):
            bx = left + 20 + (i % cols) * (box_w + 14)
            by = y + 48 + (i // cols) * (box_h + box_gap)
            box(bx, by, box_w, c, color)
        band_tops[layer_id] = (y, height)
        y += height + gap
    total_h = y + 60
    # observability alongside, as tall as the stack
    obs = next(layer for layer in catalog.layers if layer.id == "observability")
    ox, oy, oh = left + main_w + gap, 120, y - gap - 120
    color = _LAYER_COLORS["observability"]
    out.append(
        f'<rect x="{ox}" y="{oy}" width="{obs_w}" height="{oh}" rx="14" fill="{color}" fill-opacity="0.05" '
        f'stroke="{color}" stroke-opacity="0.45"/>'
    )
    out.append(
        f'<text x="{ox + 20}" y="{oy + 30}" font-size="17" font-weight="700" fill="{color}" '
        f'letter-spacing="1.5">{escape(obs.title.upper())}</text>'
    )
    for i, c in enumerate(catalog.by_layer("observability")):
        box(ox + 20, oy + 48 + i * (box_h + box_gap), obs_w - 40, c, color)
    # people and agents, and the arrow into the gateways
    head = [
        f'<rect width="{width}" height="{total_h}" fill="#050b16"/>',
        f'<text x="{left}" y="44" font-size="24" font-weight="800" fill="#f3fbff">'
        "AI Open Framework: architecture</text>",
        f'<text x="{left}" y="70" font-size="14" fill="#9fb7cc">Generated from libs/shared/src/ai_circus_shared/'
        f"architecture_catalog.yaml · dashed = optional, switched on with its make target</text>",
        f'<text x="{left + main_w / 2}" y="100" font-size="13" fill="#dcecf7" text-anchor="middle">'
        "People in a browser · chat apps and MCP clients · model providers outside the cluster</text>",
        f'<path d="M {left + main_w / 2} 106 L {left + main_w / 2} 118" stroke="#7d8fa3" stroke-width="2"/>',
    ]
    legend_y = total_h - 26
    legend = [
        f'<text x="{left}" y="{legend_y}" font-size="12" fill="#7fa3c9">Read bottom-up: the cluster, the data, what '
        f"people use, and the doors every request passes. Every service checks the caller's token and the tenant's "
        f"entitlement itself; every model call goes through llm-gateway.</text>"
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total_h}" width="{width}" height="{total_h}" '
        f'font-family="{_FONT}" role="img" aria-label="AI Open Framework architecture, by layer">\n'
        + "\n".join(head + out + legend)
        + "\n</svg>\n"
    )


def render_markdown(catalog: Catalog, adr_files: dict[int, str]) -> str:
    """docs/architecture/components.md: every component by layer, and every request flow step by step."""
    lines = [
        "# Components and request flows",
        "",
        "<!-- Generated by scripts/generate_architecture.py from libs/shared/src/ai_circus_shared/"
        "architecture_catalog.yaml. Do not edit by hand. -->",
        "",
        "The [architecture guide](README.md) explains how these fit together. Optional components are off by default"
        " and switched on with the command in *How to switch it on*.",
        "",
    ]
    for layer in catalog.layers:
        lines += [
            f"## {layer.title}",
            "",
            layer.text,
            "",
            "| Component | What it does | Technology | How to switch it on | ADR |",
            "|---|---|---|---|---|",
        ]
        for c in catalog.by_layer(layer.id):
            adrs = ", ".join(f"[{n:04d}](../adr/{adr_files[n]})" if n in adr_files else f"{n:04d}" for n in c.adr)
            on = "always on" if c.profile == "core" and not c.enable else (c.enable or c.profile)
            lines.append(f"| **{c.name}** | {c.role} | {c.tech} | {on} | {adrs or '—'} |")
        lines.append("")
    lines += ["## Request flows", ""]
    for flow in catalog.flows:
        lines += [f"### {flow.title}", "", flow.text, ""]
        names = {c.id: c.name for c in catalog.components}
        for i, step in enumerate(flow.steps, start=1):
            lines.append(f"{i}. **{names[step.source]} → {names[step.to]}**: {step.label}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---- the architecture tutorial's documents ----------------------------------------------------------------------

TUTORIAL_MAX_CHARS = 4000  # one document of the snapshot: a handful of chunks per embedding call to llm-gateway


def split_markdown(source: str, text: str, max_chars: int = TUTORIAL_MAX_CHARS) -> list[str]:
    """A markdown document as pieces of at most `max_chars`, cut at `## ` sections and then at paragraphs, each
    headed by the document's title and section and the path it came from (so a cited passage says where it is)."""
    lines = text.splitlines()
    title = next((line[2:].strip() for line in lines if line.startswith("# ")), source)
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in lines:
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif not line.startswith("# "):
            sections[-1][1].append(line)
    pieces: list[str] = []
    for heading, body in sections:
        paragraphs = [p.strip() for p in "\n".join(body).split("\n\n") if p.strip()]
        if not paragraphs:
            continue
        head = f"# {title}{' - ' + heading if heading else ''}\n\nSource: {source} (this repository)\n\n"
        current = ""
        for paragraph in paragraphs:
            if current and len(head) + len(current) + len(paragraph) + 2 > max_chars:
                pieces.append(head + current.rstrip() + "\n")
                current = ""
            current += paragraph[: max_chars - len(head)] + "\n\n"
        if current:
            pieces.append(head + current.rstrip() + "\n")
    return pieces


def scenario_components_markdown(definitions: list[Any], catalog: Catalog | None = None) -> str:
    """Which components each scenario uses and why (scenario_architecture), as one markdown document per scenario
    section, so the tutorial's assistant can answer "which services does this scenario use?"."""
    catalog = catalog or load_catalog()
    names = {c.id: c for c in catalog.components}
    flows = {f.id: f.title for f in catalog.flows}
    lines = ["# What each scenario is built from", ""]
    for definition in sorted(definitions, key=lambda d: d.slug):
        view = scenario_architecture(definition, catalog)
        lines += [
            f"## {definition.title} ({definition.slug}, {definition.kind})",
            "",
            " ".join(definition.description.split()),
            "",
        ]
        lines.append("Components it uses:")
        lines += [f"- {names[c].name} ({names[c].layer}): {view['reasons'][c]}" for c in view["components"]]
        if view["flows"]:
            lines += ["", "Request flows it follows: " + ", ".join(flows[f] for f in view["flows"]) + "."]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
