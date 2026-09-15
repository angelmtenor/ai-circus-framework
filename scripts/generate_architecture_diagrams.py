#!/usr/bin/env python3
"""Regenerate docs/screenshots/architecture-{detailed,simplified}.svg.

Pure-stdlib, deterministic: run it from the repo root after adding/removing a
service, store or monitor so the README's architecture diagrams stay in sync
with `docker-compose.yml` / `k8s/base` (the Platform dashboard's target list in
`services/data-platform-manager/.../core/platform_status.py` is the checklist
of what should appear here).

    python3 scripts/generate_architecture_diagrams.py

Both views share the same boxes and bands; they differ only in which arrows are
drawn — "detailed" shows every verified service-to-service call, "simplified"
groups them into the main data flow. The optional Data Platform profile has its
own hand-drawn supplement (architecture-data-platform.svg), not regenerated here.
"""

from __future__ import annotations

from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "screenshots"

FONT = "Arial, Helvetica, sans-serif"
BG = "#050b16"
TITLE_FILL = "#f3f8fc"
SUBTITLE_FILL = "#9fb7cc"
TECH_FILL = "#607890"
MUTED_FILL = "#5c7288"
ARROW_STROKE = "#7d8fa3"

# Pillar colours — same palette as the existing diagrams and ui-react's Tron theme.
COLORS = {
    "ingress": ("#4fc3f7", "#0a1c2a"),
    "frontend": ("#2dd4bf", "#08211d"),
    "governance": ("#b388ff", "#160f2b"),
    "ai": ("#ffca28", "#2a2008"),
    "data": ("#22d3ee", "#06232a"),
    "batch": ("#cddc39", "#20220a"),
    "external": ("#8b93a1", "#14161c"),
    "observability": ("#f472b6", "#2a0f1e"),
}

# Six columns of 300px boxes; the sixth is the admin/observability column.
COLS = [60, 390, 720, 1050, 1380, 1720]
BOX_W = 300
WIDTH = 2080
HEIGHT = 1120

ROW_TOP = 150  # Traefik
ROW_B = 300  # governance / routing / admin
ROW_C = 485  # consumption
ROW_D = 682  # stores
ROW_E = 857  # batch jobs
H_STD = 105
H_SHORT = 90


def cx(col: int) -> float:
    return COLS[col] + BOX_W / 2


# name -> (col, y, height, pillar, title, subtitle lines, tech line)
Box = tuple[int, int, int, str, str, list[str], str]
BOXES: dict[str, Box] = {
    "traefik": (2, ROW_TOP, H_SHORT, "ingress", "TRAEFIK", ["Ingress — single entrypoint, *.localhost"], "Traefik"),
    "ui": (0, ROW_B, H_STD, "frontend", "UI-REACT", ["Web app — scenarios, dashboards, chat,", "voice, Settings, admin Platform page"], "React + Vite + CopilotKit"),
    "keycloak": (1, ROW_B, H_STD, "governance", "KEYCLOAK", ["Identity — OIDC login, Organizations =", "tenants"], "Keycloak (OIDC)"),
    "registry": (2, ROW_B, H_STD, "governance", "PLATFORM-REGISTRY", ["Tenants, scenario roles, entitlement", "checks · active LLM / voice settings"], "FastAPI + SQLAlchemy"),
    "gateway": (3, ROW_B, H_STD, "ai", "LLM-GATEWAY", ["Routes every LLM call — rate limits,", "per-tenant budgets, traces to Langfuse"], "LiteLLM proxy"),
    "providers": (4, ROW_B, H_STD, "external", "LLM PROVIDERS", ["OpenAI / Gemini / Anthropic / Groq / …", "or local Ollama (optional)"], "cloud APIs · Ollama"),
    "dpm": (5, ROW_B, H_STD, "governance", "DATA-PLATFORM-MANAGER", ["Admin control plane — health probes,", "roadmap, jobs, budgets, CDC, lakehouse"], "FastAPI (ADMIN_API_KEY only)"),
    "prediction": (0, ROW_C, H_STD, "ai", "PREDICTION", ["Tabular ML — predictions + SHAP", "explainability"], "FastAPI + scikit-learn/LightGBM"),
    "assistant": (1, ROW_C, H_STD, "ai", "ASSISTANT", ["Chat copilot for tabular_ml scenarios", "(AG-UI generative UI)"], "FastAPI + LangChain"),
    "rag": (2, ROW_C, H_STD, "ai", "RAG-AGENT", ["Document Q&A over a scenario's knowledge", "base"], "FastAPI + LangChain + Qdrant"),
    "form": (3, ROW_C, H_STD, "ai", "FORM-AGENT", ["Conversational form-filling,", "RAG-grounded"], "FastAPI + LangChain + Qdrant"),
    "voice": (4, ROW_C, H_STD, "ai", "AGUI-VOICE", ["Voice mode — STT/TTS bridge into the 3", "agents (mic + speaker in the chat)"], "FastAPI + Pipecat (Whisper/Piper)"),
    "langfuse": (5, ROW_C, H_STD, "observability", "LANGFUSE", ["GenAI monitor — a trace per LLM call,", "per tenant / scenario / conversation"], "Langfuse v4 (web + worker)"),
    "postgres": (0, ROW_D, H_STD, "data", "POSTGRES", ["Keycloak · registry · documents (JSONB) ·", "agent history · Langfuse · MLflow"], "PostgreSQL"),
    "valkey": (1, ROW_D, H_STD, "data", "VALKEY", ["Cache — sessions, rate-limit & budget", "counters, Langfuse queue"], "Valkey (Redis protocol)"),
    "qdrant": (2, ROW_D, H_STD, "data", "QDRANT", ["Per-tenant vector collections"], "Qdrant"),
    "clickhouse": (3, ROW_D, H_STD, "data", "CLICKHOUSE", ["Langfuse's trace store — the only store", "the monitors add (1 GiB cap)"], "ClickHouse"),
    "seaweedfs": (4, ROW_D, H_STD, "data", "SEAWEEDFS", ["Datasets, models, documents,", "MLflow artifacts (S3-compatible)"], "SeaweedFS"),
    "mlflow": (5, ROW_D, H_STD, "observability", "MLFLOW", ["MLOps monitor — every training run's", "candidates, scores, selected model"], "MLflow (Postgres + SeaweedFS)"),
    "etl_tabular": (0, ROW_E, H_SHORT, "batch", "ETL-TABULAR", ["Prepares each scenario's tabular dataset"], "k8s Job · pandas/pyarrow"),
    "training": (1, ROW_E, H_SHORT, "batch", "TRAINING", ["Trains + registers each scenario's model"], "k8s Job · scikit-learn/LightGBM"),
    "etl_vectorize": (3, ROW_E, H_SHORT, "batch", "ETL-VECTORIZE", ["Chunks + embeds documents into Qdrant"], "k8s Job · embeddings"),
}

# (x, y, w, h, pillar, label, italic note)
BANDS = [
    (368, 102, 674, 325, "governance", "GOVERNANCE — transversal, applies across Data and AI / BI / ML alike",
     "incl. AI Gateway usage controls: per-model rate limits + per-tenant monthly budgets (live)"),
    (1050, 252, 630, 175, "ai", "AI / BI / ML — routing", ""),
    (1700, 252, 340, 557, "observability", "ADMIN & OBSERVABILITY — admin-only,",
     "ui-react's Platform page (Health + Capabilities)"),
    (38, 437, 1652, 175, "ai", "AI / BI / ML — consumption: one Deployment per scenario kind", ""),
    (38, 634, 1652, 175, "data", "DATA — source of record", ""),
    (38, 809, 1334, 160, "batch", "DATA — generation: k8s Jobs, run once or on demand", ""),
]

LEGEND = [
    ("ingress", "Ingress"),
    ("frontend", "Frontend"),
    ("governance", "Governance (transversal)"),
    ("ai", "AI / BI / ML"),
    ("data", "Data"),
    ("batch", "Batch / ETL (data)"),
    ("observability", "Admin & observability"),
    ("external", "External"),
]


def anchor(name: str, side: str) -> tuple[float, float]:
    """A point on a box's edge: top/bottom/left/right (centre of that edge)."""
    col, y, h, *_ = BOXES[name]
    x = COLS[col]
    if side == "top":
        return cx(col), y
    if side == "bottom":
        return cx(col), y + h
    if side == "left":
        return x, y + h / 2
    if side == "right":
        return x + BOX_W, y + h / 2
    raise ValueError(side)


# (from box, from side, to box, to side, primary?, label, label offset y)
Arrow = tuple[str, str, str, str, bool, str, float]

DETAILED: list[Arrow] = [
    ("traefik", "left", "ui", "top", True, "", 0),
    ("traefik", "bottom", "keycloak", "top", True, "", 0),
    ("traefik", "bottom", "prediction", "top", True, "", 0),
    ("traefik", "bottom", "assistant", "top", True, "", 0),
    ("traefik", "bottom", "rag", "top", True, "", 0),
    ("traefik", "bottom", "form", "top", True, "", 0),
    ("traefik", "right", "voice", "top", True, "", 0),
    ("traefik", "right", "dpm", "top", True, "Platform page · consoles", -8),
    ("ui", "right", "keycloak", "left", False, "OIDC login", 0),
    ("ui", "bottom", "registry", "bottom", False, "loopback, bypasses Traefik", 14),
    ("voice", "bottom", "assistant", "bottom", True, "", 0),
    ("voice", "bottom", "rag", "bottom", True, "", 0),
    ("voice", "left", "form", "right", True, "", 0),
    ("rag", "top", "keycloak", "bottom", False, "JWKS validation (every service)", -8),
    ("rag", "top", "registry", "bottom", False, "entitlement check (every service)", 14),
    ("assistant", "top", "gateway", "left", True, "", 0),
    ("rag", "top", "gateway", "left", True, "", 0),
    ("form", "top", "gateway", "left", True, "", 0),
    ("gateway", "right", "providers", "left", True, "", 0),
    ("gateway", "bottom", "langfuse", "top", True, "traces (langfuse_otel)", -6),
    ("gateway", "bottom", "valkey", "top", False, "", 0),
    ("dpm", "bottom", "voice", "top", False, "health probes (every service & store)", -10),
    ("langfuse", "bottom", "clickhouse", "top", True, "", 0),
    ("mlflow", "left", "seaweedfs", "right", False, "", 0),
    ("rag", "bottom", "qdrant", "top", True, "", 0),
    ("form", "bottom", "qdrant", "top", True, "", 0),
    ("prediction", "bottom", "seaweedfs", "top", True, "", 0),
    ("assistant", "bottom", "seaweedfs", "top", True, "", 0),
    ("assistant", "left", "postgres", "top", False, "", 0),
    ("rag", "left", "postgres", "top", False, "", 0),
    ("form", "left", "postgres", "top", False, "", 0),
    ("keycloak", "bottom", "postgres", "top", False, "", 0),
    ("registry", "top", "keycloak", "top", True, "Admin REST API", -4),
    ("etl_tabular", "right", "seaweedfs", "bottom", True, "", 0),
    ("training", "right", "seaweedfs", "bottom", True, "", 0),
    ("training", "top", "mlflow", "bottom", True, "logs every run", 0),
    ("etl_vectorize", "top", "qdrant", "bottom", True, "", 0),
]

SIMPLIFIED: list[Arrow] = [
    ("traefik", "left", "ui", "top", True, "", 0),
    ("traefik", "bottom", "keycloak", "top", True, "", 0),
    ("traefik", "bottom", "prediction", "top", True, "", 0),
    ("traefik", "bottom", "assistant", "top", True, "", 0),
    ("traefik", "bottom", "rag", "top", True, "", 0),
    ("traefik", "bottom", "form", "top", True, "", 0),
    ("traefik", "right", "voice", "top", True, "", 0),
    ("traefik", "right", "dpm", "top", True, "admin only", -8),
    ("ui", "right", "keycloak", "left", False, "OIDC login", 0),
    ("ui", "bottom", "registry", "bottom", False, "loopback port", 14),
    ("voice", "bottom", "rag", "bottom", True, "", 0),
    ("rag", "top", "registry", "bottom", False, "auth + entitlement", 6),
    ("registry", "top", "keycloak", "top", True, "seeds", -4),
    ("assistant", "top", "gateway", "left", True, "", 0),
    ("rag", "top", "gateway", "left", True, "", 0),
    ("form", "top", "gateway", "left", True, "", 0),
    ("gateway", "right", "providers", "left", True, "", 0),
    ("gateway", "bottom", "langfuse", "top", True, "traces", -6),
    ("dpm", "bottom", "voice", "top", False, "health probes", -10),
    ("langfuse", "bottom", "clickhouse", "top", True, "", 0),
    ("rag", "bottom", "qdrant", "top", True, "reads / writes", 0),
    ("prediction", "bottom", "seaweedfs", "top", True, "", 0),
    ("assistant", "left", "postgres", "top", False, "", 0),
    ("training", "right", "seaweedfs", "bottom", True, "", 0),
    ("training", "top", "mlflow", "bottom", True, "runs", 0),
    ("etl_vectorize", "top", "qdrant", "bottom", True, "", 0),
]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x: float, y: float, s: str, size: float, fill: str, *, anchor_: str = "start", weight: str = "", italic: bool = False, spacing: str = "", opacity: str = "") -> str:
    attrs = [f'x="{x:g}"', f'y="{y:g}"']
    if anchor_ != "start":
        attrs.append(f'text-anchor="{anchor_}"')
    attrs += [f'font-family="{FONT}"', f'font-size="{size:g}"']
    if weight:
        attrs.append(f'font-weight="{weight}"')
    if italic:
        attrs.append('font-style="italic"')
    if spacing:
        attrs.append(f'letter-spacing="{spacing}"')
    attrs.append(f'fill="{fill}"')
    if opacity:
        attrs.append(f'fill-opacity="{opacity}"')
    return f"<text {' '.join(attrs)}>{esc(s)}</text>"


def band_svg() -> list[str]:
    out = []
    for x, y, w, h, pillar, label, note in BANDS:
        stroke = COLORS[pillar][0]
        out.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{stroke}" fill-opacity="0.035" '
            f'stroke="{stroke}" stroke-opacity="0.35" stroke-width="1.3" stroke-dasharray="3 4"/>'
        )
        out.append(text(x + 16, y + 18, label, 11, stroke, weight="700", spacing="0.8", opacity="0.85"))
        if note:
            out.append(text(x + 16, y + 34, note, 10, stroke, italic=True, spacing="0.3", opacity="0.55"))
    return out


def box_rects() -> list[str]:
    out = []
    for col, y, h, pillar, *_ in BOXES.values():
        stroke, fill = COLORS[pillar]
        x = COLS[col]
        out.append(f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.8" filter="url(#glow)" opacity="0.98"/>')
        out.append(f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{h}" rx="12" fill="none" stroke="{stroke}" stroke-width="1.1" opacity="0.9"/>')
    return out


def box_texts() -> list[str]:
    out = []
    for col, y, h, pillar, title, lines, tech in BOXES.values():
        stroke = COLORS[pillar][0]
        x = COLS[col]
        c = cx(col)
        out.append(text(c, y + 30, title, 17, TITLE_FILL, anchor_="middle", weight="700", spacing="0.5"))
        if h == H_SHORT:
            out.append(text(c, y + 52, lines[0], 11.5, SUBTITLE_FILL, anchor_="middle"))
            sep, tech_y = y + 64, y + 80
        else:
            if len(lines) == 1:
                out.append(text(c, y + 67, lines[0], 11.5, SUBTITLE_FILL, anchor_="middle"))
            else:
                out.append(text(c, y + 52, lines[0], 11.5, SUBTITLE_FILL, anchor_="middle"))
                out.append(text(c, y + 67, lines[1], 11.5, SUBTITLE_FILL, anchor_="middle"))
            sep, tech_y = y + 79, y + 95
        out.append(f'<line x1="{x + 18}" y1="{sep}" x2="{x + BOX_W - 18}" y2="{sep}" stroke="{stroke}" stroke-opacity="0.25"/>')
        out.append(text(c, tech_y, tech, 10, TECH_FILL, anchor_="middle", italic=True))
    return out


def arrow_svg(arrows: list[Arrow]) -> list[str]:
    out = []
    for src, s_side, dst, d_side, primary, label, dy in arrows:
        x1, y1 = anchor(src, s_side)
        x2, y2 = anchor(dst, d_side)
        # Cubic S-curve: leave/enter each box perpendicular to its edge.
        if s_side in ("top", "bottom") and d_side in ("top", "bottom"):
            # Both vertical: a shared horizontal "bus" halfway; same-side edges (e.g.
            # bottom -> bottom) bow outwards below/above the boxes instead.
            if s_side == d_side:
                bow = 18 if s_side == "bottom" else -18
                my = max(y1, y2) + bow if s_side == "bottom" else min(y1, y2) + bow
                d = f"M {x1:g} {y1:g} C {x1:g} {my:g} {x2:g} {my:g} {x2:g} {y2:g}"
            else:
                my = (y1 + y2) / 2
                d = f"M {x1:g} {y1:g} C {x1:g} {my:g} {x2:g} {my:g} {x2:g} {y2:g}"
        elif s_side in ("left", "right") and d_side in ("left", "right"):
            mx = (x1 + x2) / 2
            d = f"M {x1:g} {y1:g} C {mx:g} {y1:g} {mx:g} {y2:g} {x2:g} {y2:g}"
        elif s_side in ("left", "right"):
            # Leave horizontally, arrive vertically: an L-shaped bend.
            d = f"M {x1:g} {y1:g} C {(x1 + x2) / 2:g} {y1:g} {x2:g} {(y1 + y2) / 2:g} {x2:g} {y2:g}"
        else:
            # Leave vertically, arrive horizontally.
            d = f"M {x1:g} {y1:g} C {x1:g} {(y1 + y2) / 2:g} {(x1 + x2) / 2:g} {y2:g} {x2:g} {y2:g}"
        if primary:
            out.append(f'<path d="{d}" fill="none" stroke="{ARROW_STROKE}" stroke-width="2.2" opacity="0.8" marker-end="url(#arrow-primary)"/>')
        else:
            out.append(f'<path d="{d}" fill="none" stroke="{ARROW_STROKE}" stroke-width="1.1" opacity="0.42" stroke-dasharray="6 5" marker-end="url(#arrow-secondary)"/>')
        if label:
            lx, ly = (x1 + x2) / 2, (y1 + y2) / 2 + dy
            w = len(label) * 5.9 + 16
            out.append(f'<rect x="{lx - w / 2:g}" y="{ly - 8:g}" width="{w:g}" height="16" rx="4" fill="{BG}" opacity="0.92"/>')
            out.append(text(lx, ly + 4.5, label, 9.5, ARROW_STROKE, anchor_="middle", opacity="0.95"))
    return out


def legend_svg(y: float) -> list[str]:
    out = []
    x = 300.0
    for pillar, label in LEGEND:
        out.append(f'<circle cx="{x:g}" cy="{y:g}" r="6.5" fill="{COLORS[pillar][0]}"/>')
        out.append(text(x + 15, y + 4, label, 12, SUBTITLE_FILL))
        x += 15 + len(label) * 6.6 + 28
    return out


def render(subtitle: str, arrows: list[Arrow]) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        "<defs>",
        '  <filter id="glow" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur in="SourceGraphic" stdDeviation="3.2" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>',
        '  <pattern id="grid" width="34" height="34" patternUnits="userSpaceOnUse"><path d="M 34 0 L 0 0 0 34" fill="none" stroke="rgba(255,255,255,0.035)" stroke-width="1"/></pattern>',
        '  <marker id="arrow-primary" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#9fb7cc" opacity="0.85"/></marker>',
        '  <marker id="arrow-secondary" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#7d8fa3" opacity="0.6"/></marker>',
        "</defs>",
        f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="{BG}"/>',
        f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="url(#grid)"/>',
        text(WIDTH / 2, 38, "AI OPEN FRAMEWORK", 26, TITLE_FILL, anchor_="middle", weight="800", spacing="2"),
        text(WIDTH / 2, 58, subtitle, 12.5, MUTED_FILL, anchor_="middle", spacing="0.5"),
    ]
    parts += band_svg()
    parts += box_rects()
    parts += arrow_svg(arrows)
    parts += box_texts()
    parts += legend_svg(1000)
    parts.append(text(WIDTH / 2, 1068, "Runs identically on Kubernetes (k3s), namespace ai-circus, and via docker compose — solid arrows: primary request/data paths; dotted: cross-cutting auth/admin/monitoring calls", 11, MUTED_FILL, anchor_="middle"))
    parts.append(text(WIDTH / 2, 1088, "Admin-only monitors: Langfuse (langfuse.localhost) and MLflow (mlflow.localhost) reuse Postgres/Valkey/SeaweedFS — see architecture-data-platform.svg for the optional Data Platform profile (Kafka, CDC, lakehouse, semantic layer)", 10.5, MUTED_FILL, anchor_="middle"))
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    (OUT_DIR / "architecture-detailed.svg").write_text(render("Realistic view — verified service-to-service calls", DETAILED))
    (OUT_DIR / "architecture-simplified.svg").write_text(render("Simplified view — grouped data flow", SIMPLIFIED))
    print(f"wrote {OUT_DIR}/architecture-detailed.svg and architecture-simplified.svg")


if __name__ == "__main__":
    main()
