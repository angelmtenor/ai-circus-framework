#!/usr/bin/env python3
"""Regenerate docs/screenshots/architecture-layers.svg — the three-layer overview.

Pure-stdlib, deterministic; same visual language as generate_architecture_diagrams.py. Run from the
repo root when a service is added or moves between layers:

    python3 scripts/generate_layers_diagram.py

Layers: 1 consumption (AI, interactive dashboards, GenAI), 2 data platform, 3 AI gateway &
governance. The two other diagrams (architecture-detailed.svg, architecture-data-platform.svg)
zoom into the same services.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots" / "architecture-layers.svg"
FONT = "Arial, Helvetica, sans-serif"
W, H = 1740, 1010
BW, BH, GAP = 190, 86, 14
X0 = 60
COLORS = {
    "ai": ("#ffca28", "#2a2008"),
    "data": ("#22d3ee", "#06232a"),
    "batch": ("#cddc39", "#20220a"),
    "governance": ("#b388ff", "#160f2b"),
    "external": ("#8b93a1", "#14161c"),
    "observability": ("#f472b6", "#2a0f1e"),
    "ingress": ("#4fc3f7", "#0a1c2a"),
}
# (accent, number, title, subtitle, y, height)
BANDS = [
    ("ai", "1", "CONSUMPTION", "AI predictions · interactive dashboards · GenAI chat, RAG, forms and voice — what people use", 84, 240),
    ("data", "2", "DATA PLATFORM", "capture → store → transform → serve; trains the models and indexes the documents", 392, 286),
    ("governance", "3", "AI GATEWAY & GOVERNANCE", "every request and every model call passes here — identity, tenancy, routing, policy, audit, monitoring", 724, 232),
]
Box = tuple[str, str, list[str], str]  # pillar, title, lines, tech/tier
ROW_L1: list[Box] = [
    ("ai", "UI-REACT", ["Scenario gallery, showpiece", "dashboards, chat, tutorials,", "data-product workspace"], "React · AG-UI · CopilotKit"),
    ("ai", "PREDICTION", ["Tabular ML: predictions,", "SHAP, what-ifs, out-of-fold", "scores"], "FastAPI · LightGBM"),
    ("ai", "ASSISTANT", ["Chat copilot with tools:", "governed data, catalogue,", "ml_predict, documents"], "FastAPI · LangChain"),
    ("ai", "RAG-AGENT", ["Document Q&A and GraphRAG", "with cited sources"], "FastAPI · Qdrant"),
    ("ai", "FORM-AGENT", ["Assisted forms: the assistant", "fills, validates and prints", "official sheets"], "FastAPI · reportlab"),
    ("ai", "AGUI-VOICE", ["Voice mode into the three", "agents (speech in and out)"], "Pipecat · Whisper · Piper"),
    ("ai", "DL-INFERENCE", ["Vision and NLP models,", "heatmaps, anomaly maps"], "onnxruntime · optional"),
    ("frontend", "SUPERSET", ["BI dashboards as code with", "row-level security"], "optional bi tier"),
]
ROW_L2A: list[Box] = [
    ("external", "SOURCES", ["Tills and web orders, daily", "files, an ERP database"], "scenario simulator plugin"),
    ("data", "KAFKA · CDC · LANDING", ["A topic per tenant, change", "capture, raw file landing"], "Kafka · SeaweedFS"),
    ("batch", "DATA-INGEST", ["JSON Schema contracts,", "quarantine, idempotent upsert,", "hot aggregates"], "FastAPI workers"),
    ("data", "BRONZE · LAKE", ["Raw records by key, and", "an Iceberg archive"], "Postgres · Iceberg"),
    ("batch", "DATA-TRANSFORM", ["dbt silver then gold with", "tests; Dagster sensors"], "dbt · Dagster (analytics)"),
    ("data", "MARTS", ["Gold marts and feature", "marts for the ML models"], "Postgres · ClickHouse"),
    ("ai", "DATA-API", ["One governed door: metrics,", "records, SQL, lineage, SSE"], "FastAPI · sqlglot"),
    ("observability", "OPENMETADATA", ["Catalogue, contracts and", "lineage end to end"], "optional governance tier"),
]
ROW_L2B: list[Box] = [
    ("batch", "ETL-TABULAR", ["Cleans and prepares each", "tabular dataset"], "k8s Job · pandas"),
    ("batch", "TRAINING", ["Trains, selects and", "registers each model"], "k8s Job · scikit-learn"),
    ("batch", "DL-TRAINING", ["Fine-tunes vision and NLP", "models (GPU optional)"], "k8s Job · PyTorch"),
    ("batch", "ETL-VECTORIZE", ["Chunks and embeds documents", "and knowledge graphs"], "k8s Job · embeddings"),
    ("data", "QDRANT", ["Per-tenant vector", "collections"], "Qdrant"),
    ("data", "SEAWEEDFS", ["Datasets, models, documents,", "lake files (S3)"], "SeaweedFS"),
    ("data", "POSTGRES", ["Registry, conversations,", "bronze, silver and gold"], "PostgreSQL"),
    ("data", "VALKEY", ["Caches, rate-limit and", "budget counters"], "Valkey"),
]
ROW_L3: list[Box] = [
    ("ingress", "TRAEFIK", ["The only ingress,", "*.localhost hostnames"], "Traefik"),
    ("governance", "KEYCLOAK", ["Identity (OIDC); a tenant is", "a Keycloak Organization"], "Keycloak"),
    ("governance", "PLATFORM-REGISTRY", ["Tenants, entitlements, audit", "trail, AI-BOM, governance", "overview"], "FastAPI · Postgres"),
    ("ai", "LLM-GATEWAY", ["Routes every model call: rate", "limits, budgets, policy", "decision point, traces"], "LiteLLM proxy"),
    ("external", "LLM PROVIDERS", ["OpenAI, Gemini, Anthropic,", "Groq, DeepSeek or local"], "cloud APIs · Ollama"),
    ("observability", "LANGFUSE · MLFLOW", ["GenAI traces per call; every", "training run and its metrics"], "admin monitors"),
    ("governance", "DATA-PLATFORM-MANAGER", ["Platform page: health,", "monitor, capabilities, the", "Governance tab"], "FastAPI · admin only"),
    ("governance", "OPEN CONTRACTS", ["CloudEvents, hash-chained", "audit, CycloneDX AI-BOM,", "OPA-compatible policy"], "all off by default"),
]


def box(x: float, y: float, b: Box) -> str:
    pillar, title, lines, tech = b
    stroke, fill = COLORS.get(pillar, COLORS["data"] if pillar == "frontend" else COLORS["ai"])
    if pillar == "frontend":
        stroke, fill = "#2dd4bf", "#08211d"
    dash = ' stroke-dasharray="5 4"' if tech.startswith("optional") or "optional" in tech else ""
    out = [f'<rect x="{x}" y="{y}" width="{BW}" height="{BH}" rx="9" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>']
    out.append(f'<text x="{x + 10}" y="{y + 18}" font-family="{FONT}" font-size="11" font-weight="800" letter-spacing="0.5" fill="{stroke}">{escape(title)}</text>')
    for i, line in enumerate(lines):
        out.append(f'<text x="{x + 10}" y="{y + 34 + i * 13}" font-family="{FONT}" font-size="10.5" fill="#c9d8e6">{escape(line)}</text>')
    out.append(f'<text x="{x + 10}" y="{y + BH - 7}" font-family="{FONT}" font-size="9" fill="#607890">{escape(tech)}</text>')
    return "\n".join(out)


def row(boxes: list[Box], y: float) -> list[str]:
    return [box(X0 + i * (BW + GAP), y, b) for i, b in enumerate(boxes)]


def arrows_between(y_from: float, y_to: float, label: str) -> list[str]:
    """Double-headed arrows across the gap between two bands, with a label in the middle."""
    mid = (y_from + y_to) / 2
    out = [
        f'<line x1="{x}" y1="{y_from}" x2="{x}" y2="{y_to}" stroke="#9fb7cc" stroke-opacity="0.8" stroke-width="1.6" '
        'marker-start="url(#a)" marker-end="url(#a)"/>'
        for x in (190, 1550)
    ]
    out.append(
        f'<text x="{W / 2}" y="{mid + 4}" text-anchor="middle" font-family="{FONT}" font-size="11.5" font-style="italic" fill="#9fb7cc">{escape(label)}</text>'
    )
    return out


def build() -> str:
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#9fb7cc"/></marker></defs>',
        f'<rect width="{W}" height="{H}" fill="#050b16"/>',
        f'<text x="{W / 2}" y="34" text-anchor="middle" font-family="{FONT}" font-size="26" font-weight="800" letter-spacing="2" fill="#f3f8fc">AI OPEN FRAMEWORK</text>',
        f'<text x="{W / 2}" y="56" text-anchor="middle" font-family="{FONT}" font-size="12.5" letter-spacing="0.5" fill="#5c7288">Three layers: what people use, the data it stands on, and the gateway and governance every request passes through</text>',
    ]
    for accent, num, title, subtitle, y, h in BANDS:
        stroke, _ = COLORS[accent]
        p.append(f'<rect x="38" y="{y}" width="1664" height="{h}" rx="14" fill="{stroke}" fill-opacity="0.04" stroke="{stroke}" stroke-opacity="0.55" stroke-width="1.3"/>')
        p.append(f'<circle cx="68" cy="{y + 24}" r="13" fill="{stroke}"/>')
        p.append(f'<text x="68" y="{y + 29}" text-anchor="middle" font-family="{FONT}" font-size="14" font-weight="800" fill="#050b16">{num}</text>')
        p.append(f'<text x="92" y="{y + 29}" font-family="{FONT}" font-size="14" font-weight="800" letter-spacing="1.5" fill="{stroke}">{escape(title)}</text>')
        p.append(f'<text x="{92 + len(title) * 11 + 18}" y="{y + 29}" font-family="{FONT}" font-size="11.5" fill="#9fb7cc">{escape(subtitle)}</text>')
    p += row(ROW_L1, 138)
    p.append(f'<text x="{X0}" y="258" font-family="{FONT}" font-size="11" fill="#9fb7cc">Five scenario kinds, one service instance per kind — tabular ML, RAG and GraphRAG, assisted forms, deep learning, and data products — each rendered by generic tabs plus an optional showpiece tab.</text>')
    p.append(f'<text x="{X0}" y="276" font-family="{FONT}" font-size="11" fill="#9fb7cc">Interactive dashboards: the generic tabs, the showpiece tabs (maps, networks, globes), the data-product workspace and Superset. GenAI: the chat agents, all through the gateway below.</text>')
    p.append(f'<text x="{X0}" y="296" font-family="{FONT}" font-size="11" font-style="italic" fill="#607890">A scenario is a YAML file: it adds product content, never new UI or service code.</text>')
    p += row(ROW_L2A, 440)
    for i in range(len(ROW_L2A) - 1):
        x = X0 + i * (BW + GAP) + BW
        p.append(f'<line x1="{x}" y1="483" x2="{x + GAP}" y2="483" stroke="#7d8fa3" stroke-width="1.4" marker-end="url(#a)"/>')
    p += row(ROW_L2B, 552)
    p.append(f'<text x="{X0}" y="660" font-family="{FONT}" font-size="11" fill="#9fb7cc">Row 1: the streaming data path of a data product (optional tiers: core, analytics, bi, governance). Row 2: batch jobs that train models and index documents, and the stores everything lives in — all scoped by org_id.</text>')
    p += arrows_between(324, 392, "reads governed data, models and vectors — always scoped to the caller's tenant")
    p += arrows_between(678, 724, "every service checks identity and entitlement, and every LLM call goes through the gateway")
    p += row(ROW_L3, 770)
    p.append(f'<text x="{X0}" y="878" font-family="{FONT}" font-size="11" fill="#9fb7cc">Tenancy: the tenant is a Keycloak Organization; the entitlement check happens in the backend, never only in the UI. Governance supports a management system — it is not one.</text>')
    p.append(f'<text x="{X0}" y="896" font-family="{FONT}" font-size="11" fill="#9fb7cc">Governance-ready by standards: declared AI-system metadata, a policy decision point, content-free CloudEvents, a hash-chained audit trail, decision records and an AI-BOM. Everything is off by default.</text>')
    p.append(f'<text x="{X0}" y="936" font-family="{FONT}" font-size="10.5" fill="#5c7288">Dashed border = optional. See architecture-detailed.svg for every service-to-service call and architecture-data-platform.svg for the data path in detail.</text>')
    p.append("</svg>")
    return "\n".join(p) + "\n"


if __name__ == "__main__":
    OUT.write_text(build())
    print(f"wrote {OUT}")
