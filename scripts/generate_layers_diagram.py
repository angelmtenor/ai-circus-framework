#!/usr/bin/env python3
"""Regenerate docs/screenshots/architecture-layers.svg — the three-layer overview, read left to right.

Pure-stdlib, deterministic; same visual language as generate_architecture_diagrams.py. Run from the
repo root when a service is added or moves between layers:

    python3 scripts/generate_layers_diagram.py

Layers, as columns: 1 consumption (AI, interactive dashboards, GenAI), 2 data platform, 3 AI gateway &
governance — on top of a horizontal band, the platform's observability stack, which every workload on
the cluster shares and the framework only plugs into (ADR 0015). The two other diagrams
(architecture-detailed.svg, architecture-data-platform.svg) zoom into the same services.
"""

from __future__ import annotations

import textwrap
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots" / "architecture-layers.svg"
FONT = "Arial, Helvetica, sans-serif"
BW, BH, GAP = 190, 86, 14
ROW_STEP = BH + GAP
PAD = 20  # inside a column, around its boxes
SUB_GAP = 34  # between the data column's two sub-blocks
COL_GAP = 120  # between columns: room for the arrow and its label
TOP = 84
COL_H = 600
OBS_Y = TOP + COL_H + 64
OBS_H = 196
W = 40 + (2 * BW + GAP + 2 * PAD) + COL_GAP + (4 * BW + 2 * GAP + SUB_GAP + 2 * PAD) + COL_GAP + (2 * BW + GAP + 2 * PAD) + 40
H = OBS_Y + OBS_H + 54
COLORS = {
    "ai": ("#ffca28", "#2a2008"),
    "data": ("#22d3ee", "#06232a"),
    "batch": ("#cddc39", "#20220a"),
    "governance": ("#b388ff", "#160f2b"),
    "external": ("#8b93a1", "#14161c"),
    "observability": ("#f472b6", "#2a0f1e"),
    "ingress": ("#4fc3f7", "#0a1c2a"),
    "frontend": ("#2dd4bf", "#08211d"),
}
Box = tuple[str, str, list[str], str]  # pillar, title, lines, tech/tier
L1: list[Box] = [
    ("ai", "UI-REACT", ["Scenario gallery, showpiece", "dashboards, chat, tutorials,", "data-product workspace"], "React · AG-UI · CopilotKit"),
    ("ai", "PREDICTION", ["Tabular ML: predictions,", "SHAP, what-ifs, out-of-fold", "scores"], "FastAPI · LightGBM"),
    ("ai", "ASSISTANT", ["Chat copilot with tools:", "governed data, catalogue,", "ml_predict, documents"], "FastAPI · LangChain"),
    ("ai", "RAG-AGENT", ["Document Q&A and GraphRAG", "with cited sources"], "FastAPI · Qdrant"),
    ("ai", "FORM-AGENT", ["Assisted forms: the assistant", "fills, validates and prints", "official sheets"], "FastAPI · reportlab"),
    ("ai", "AGUI-VOICE", ["Voice mode into the three", "agents (speech in and out)"], "Pipecat · Whisper · Piper"),
    ("ai", "DL-INFERENCE", ["Vision and NLP models,", "heatmaps, anomaly maps"], "onnxruntime · optional"),
    ("frontend", "SUPERSET", ["BI dashboards as code with", "row-level security"], "optional bi tier"),
]
L2_STREAM: list[Box] = [
    ("external", "SOURCES", ["Tills and web orders, daily", "files, an ERP database"], "scenario simulator plugin"),
    ("data", "KAFKA · CDC · LANDING", ["A topic per tenant, change", "capture, raw file landing"], "Kafka · SeaweedFS"),
    ("batch", "DATA-INGEST", ["JSON Schema contracts,", "quarantine, idempotent upsert,", "hot aggregates"], "FastAPI workers"),
    ("data", "BRONZE · LAKE", ["Raw records by key, and", "an Iceberg archive"], "Postgres · Iceberg"),
    ("batch", "DATA-TRANSFORM", ["dbt silver then gold with", "tests; Dagster sensors"], "dbt · Dagster (analytics)"),
    ("data", "MARTS", ["Gold marts and feature", "marts for the ML models"], "Postgres · ClickHouse"),
    ("ai", "DATA-API", ["One governed door: metrics,", "records, SQL, lineage, SSE"], "FastAPI · sqlglot"),
    ("observability", "OPENMETADATA", ["Catalogue, contracts and", "lineage end to end"], "optional governance tier"),
]
L2_BATCH: list[Box] = [
    ("batch", "ETL-TABULAR", ["Cleans and prepares each", "tabular dataset"], "k8s Job · pandas"),
    ("data", "QDRANT", ["Per-tenant vector", "collections"], "Qdrant"),
    ("batch", "TRAINING", ["Trains, selects and", "registers each model"], "k8s Job · scikit-learn"),
    ("data", "SEAWEEDFS", ["Datasets, models, documents,", "lake files (S3)"], "SeaweedFS"),
    ("batch", "DL-TRAINING", ["Fine-tunes vision and NLP", "models (GPU optional)"], "k8s Job · PyTorch"),
    ("data", "POSTGRES", ["Registry, conversations,", "bronze, silver and gold"], "PostgreSQL"),
    ("batch", "ETL-VECTORIZE", ["Chunks and embeds documents", "and knowledge graphs"], "k8s Job · embeddings"),
    ("data", "VALKEY", ["Caches, rate-limit and", "budget counters"], "Valkey"),
]
L3: list[Box] = [
    ("ingress", "TRAEFIK", ["The only ingress,", "*.localhost hostnames"], "Traefik"),
    ("governance", "KEYCLOAK", ["Identity (OIDC); a tenant is", "a Keycloak Organization"], "Keycloak"),
    ("governance", "PLATFORM-REGISTRY", ["Tenants, entitlements, audit", "trail, AI-BOM, governance", "overview"], "FastAPI · Postgres"),
    ("ai", "LLM-GATEWAY", ["Routes every model call: rate", "limits, budgets, policy", "decision point, traces"], "LiteLLM proxy"),
    ("external", "LLM PROVIDERS", ["OpenAI, Gemini, Anthropic,", "Groq, DeepSeek or local"], "cloud APIs · Ollama"),
    ("observability", "LANGFUSE · MLFLOW", ["LLM traces with content;", "every training run and", "its metrics"], "AI-specific monitors"),
    ("governance", "DATA-PLATFORM-MANAGER", ["Platform page: health,", "monitor, capabilities, the", "Governance tab"], "FastAPI · admin only"),
    ("governance", "OPEN CONTRACTS", ["CloudEvents, hash-chained", "audit, CycloneDX AI-BOM,", "OPA-compatible policy"], "all off by default"),
]
PLATFORM: list[Box] = [
    ("external", "OTEL COLLECTOR", ["Receives OTLP traces, scrapes", "/metrics, tails JSON logs"], "platform prerequisite"),
    ("external", "METRICS STORE", ["Service, gateway and GPU", "metrics; alert rules"], "VictoriaMetrics · Prometheus"),
    ("external", "LOKI", ["JSON log lines, joined to", "traces by trace_id"], "platform prerequisite"),
    ("external", "TEMPO", ["Content-free traces; same", "trace id as in Langfuse"], "platform prerequisite"),
    ("external", "GRAFANA", ["Platform dashboards, plus", "the framework's AI ones"], "platform prerequisite"),
    ("external", "GPU METRICS", ["DCGM exporter where", "supported, NVML fallback"], "platform prerequisite"),
]
# (accent, number, title, subtitle, boxes as [(block, columns)], notes)
COLUMNS = [
    (
        "ai",
        "1",
        "CONSUMPTION",
        "AI predictions · interactive dashboards · GenAI chat, RAG, forms and voice — what people use",
        [L1],
        [
            "Five scenario kinds, one service instance per kind, each rendered by generic tabs plus an optional "
            "showpiece tab (maps, networks, globes). A scenario is a YAML file: content, never new UI or service code.",
        ],
    ),
    (
        "data",
        "2",
        "DATA PLATFORM",
        "capture → store → transform → serve; trains the models and indexes the documents",
        [L2_STREAM, L2_BATCH],
        [
            "Left: the streaming path of a data product, in order (optional tiers: core, analytics, bi, governance). "
            "Right: batch jobs that train models and index documents, and the stores everything lives in — "
            "all scoped by org_id.",
        ],
    ),
    (
        "governance",
        "3",
        "AI GATEWAY & GOVERNANCE",
        "identity, tenancy, routing, policy, audit — every request and every model call passes here",
        [L3],
        [
            "The entitlement check happens in the backend, never only in the UI. Governance-ready by standards, "
            "everything off by default: it supports a management system — it is not one.",
        ],
    ),
]


def text(x: float, y: float, s: str, *, size: float = 11, fill: str = "#9fb7cc", extra: str = "") -> str:
    return f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" fill="{fill}"{extra}>{escape(s)}</text>'


def box(x: float, y: float, b: Box) -> str:
    pillar, title, lines, tech = b
    stroke, fill = COLORS[pillar]
    dash = ' stroke-dasharray="5 4"' if "optional" in tech else ""
    out = [f'<rect x="{x}" y="{y}" width="{BW}" height="{BH}" rx="9" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>']
    out.append(text(x + 10, y + 18, title, size=11, fill=stroke, extra=' font-weight="800" letter-spacing="0.5"'))
    out += [text(x + 10, y + 34 + i * 13, line, size=10.5, fill="#c9d8e6") for i, line in enumerate(lines)]
    out.append(text(x + 10, y + BH - 7, tech, size=9, fill="#607890"))
    return "\n".join(out)


def grid(x0: float, y0: float, boxes: list[Box], *, column_major: bool) -> tuple[list[str], list[tuple[float, float]]]:
    """Lay `boxes` out two wide; column-major keeps a sequence reading down, then across."""
    out, at = [], []
    rows = (len(boxes) + 1) // 2
    for i, b in enumerate(boxes):
        col, row = (i // rows, i % rows) if column_major else (i % 2, i // 2)
        x, y = x0 + col * (BW + GAP), y0 + row * ROW_STEP
        out.append(box(x, y, b))
        at.append((x, y))
    return out, at


def chain_arrows(at: list[tuple[float, float]]) -> list[str]:
    """Arrows along a column-major sequence: down each column, then an elbow to the next one's top."""
    out = []
    for (x1, y1), (x2, y2) in zip(at, at[1:], strict=False):
        if x1 == x2:
            out.append(f'<line x1="{x1 + BW / 2}" y1="{y1 + BH}" x2="{x2 + BW / 2}" y2="{y2}" stroke="#7d8fa3" stroke-width="1.4" marker-end="url(#a)"/>')
        else:
            mid = x1 + BW + GAP / 2
            out.append(
                f'<path d="M {x1 + BW} {y1 + BH / 2} H {mid} V {y2 + BH / 2} H {x2}" fill="none" stroke="#7d8fa3" '
                'stroke-width="1.4" marker-end="url(#a)"/>'
            )
    return out


def link(x_from: float, x_to: float, y: float, label: str) -> list[str]:
    """A double-headed arrow across the gap between two columns, its label wrapped underneath."""
    out = [
        f'<line x1="{x_from + 6}" y1="{y}" x2="{x_to - 6}" y2="{y}" stroke="#9fb7cc" stroke-opacity="0.8" stroke-width="1.6" '
        'marker-start="url(#a)" marker-end="url(#a)"/>'
    ]
    mid = (x_from + x_to) / 2
    for i, line in enumerate(textwrap.wrap(label, 19)):
        out.append(text(mid, y + 22 + i * 14, line, size=10.5, extra=' text-anchor="middle" font-style="italic"'))
    return out


def build() -> str:
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#9fb7cc"/></marker></defs>',
        f'<rect width="{W}" height="{H}" fill="#050b16"/>',
        text(W / 2, 34, "AI OPEN FRAMEWORK", size=26, fill="#f3f8fc", extra=' text-anchor="middle" font-weight="800" letter-spacing="2"'),
        text(
            W / 2,
            56,
            "Three layers, left to right: what people use, the data it stands on, and the gateway and governance every "
            "request passes through — on the platform's shared observability stack",
            size=12.5,
            fill="#5c7288",
            extra=' text-anchor="middle" letter-spacing="0.5"',
        ),
    ]
    x = 40.0
    spans: list[tuple[float, float]] = []
    for accent, num, title, subtitle, blocks, notes in COLUMNS:
        stroke, _ = COLORS[accent]
        inner = len(blocks) * (2 * BW + GAP) + (len(blocks) - 1) * SUB_GAP
        width = inner + 2 * PAD
        spans.append((x, x + width))
        p.append(f'<rect x="{x}" y="{TOP}" width="{width}" height="{COL_H}" rx="14" fill="{stroke}" fill-opacity="0.04" stroke="{stroke}" stroke-opacity="0.55" stroke-width="1.3"/>')
        p.append(f'<circle cx="{x + 30}" cy="{TOP + 26}" r="13" fill="{stroke}"/>')
        p.append(text(x + 30, TOP + 31, num, size=14, fill="#050b16", extra=' text-anchor="middle" font-weight="800"'))
        p.append(text(x + 54, TOP + 31, title, size=14, fill=stroke, extra=' font-weight="800" letter-spacing="1.5"'))
        wrap = int(inner / 6.2)
        for i, line in enumerate(textwrap.wrap(subtitle, wrap)):
            p.append(text(x + PAD, TOP + 56 + i * 15, line, size=11.5))
        y0 = TOP + 96
        bx = x + PAD
        for block in blocks:
            sequence = block is L2_STREAM
            boxes, at = grid(bx, y0, block, column_major=sequence)
            p += boxes
            if sequence:
                p += chain_arrows(at)
            bx += 2 * BW + GAP + SUB_GAP
        ny = y0 + 4 * ROW_STEP + 8
        for note in notes:
            for i, line in enumerate(textwrap.wrap(note, wrap)):
                p.append(text(x + PAD, ny + i * 15, line))
        x += width + COL_GAP
    (a0, a1), (b0, b1), (c0, c1) = spans
    link_y = TOP + 96 + 2 * ROW_STEP - GAP / 2
    p += link(a1, b0, link_y, "reads governed data, models and vectors — scoped to the caller's tenant")
    p += link(b1, c0, link_y, "every service checks identity and entitlement; every LLM call goes through the gateway")

    # The platform's observability band, under all three columns.
    stroke, _ = COLORS["observability"]
    for left, right in spans:
        cx = (left + right) / 2
        p.append(f'<line x1="{cx}" y1="{TOP + COL_H}" x2="{cx}" y2="{OBS_Y - 2}" stroke="{stroke}" stroke-opacity="0.8" stroke-width="1.6" marker-end="url(#a)"/>')
        p.append(text(cx + 10, TOP + COL_H + 36, "/metrics · OTLP traces · JSON logs", size=10.5, fill=stroke, extra=' font-style="italic"'))
    p.append(f'<rect x="40" y="{OBS_Y}" width="{W - 80}" height="{OBS_H}" rx="14" fill="{stroke}" fill-opacity="0.04" stroke="{stroke}" stroke-opacity="0.55" stroke-width="1.3" stroke-dasharray="7 5"/>')
    p.append(text(60, OBS_Y + 30, "PLATFORM OBSERVABILITY", size=14, fill=stroke, extra=' font-weight="800" letter-spacing="1.5"'))
    p.append(
        text(
            300,
            OBS_Y + 30,
            "shared by everything on the Kubernetes cluster — a prerequisite the framework plugs into, not part of it (ADR 0015)",
            size=11.5,
        )
    )
    contract = [
        "THE CONTRACT EVERY SERVICE HONOURS",
        "• /metrics in Prometheus format",
        "• OTLP traces when the platform sets an endpoint",
        "   (off otherwise)",  # SVG collapses plain spaces
        "• JSON logs with trace_id and span_id",
        "• no prompts or personal data in spans or logs:",
        "   those stay in Langfuse, linked by trace id",
    ]
    for i, line in enumerate(contract):
        bold = ' font-weight="800" letter-spacing="0.5"' if i == 0 else ""
        p.append(text(60, OBS_Y + 64 + i * 15, line, size=11 if i == 0 else 10.5, fill=stroke if i == 0 else "#c9d8e6", extra=bold))
    px = 400.0
    for b in PLATFORM:
        p.append(box(px, OBS_Y + 54, b))
        px += BW + GAP
    p.append(
        text(
            400,
            OBS_Y + OBS_H - 18,
            "The framework ships only its side: the contract above, then AI dashboards (gateway tokens, cost and latency, "
            "GPU, model drift), alert rules, a collector snippet and an optional lab bundle, off by default.",
            size=10.5,
            fill="#607890",
        )
    )
    p.append(
        text(
            40,
            H - 18,
            "Dashed border = optional. See architecture-detailed.svg for every service-to-service call and "
            "architecture-data-platform.svg for the data path in detail.",
            size=10.5,
            fill="#5c7288",
        )
    )
    p.append("</svg>")
    return "\n".join(p) + "\n"


if __name__ == "__main__":
    OUT.write_text(build())
    print(f"wrote {OUT}")
