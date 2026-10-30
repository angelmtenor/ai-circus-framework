#!/usr/bin/env python3
"""Regenerate docs/screenshots/architecture-data-platform.svg.

Pure-stdlib, deterministic; same visual language as generate_architecture_diagrams.py.
Run from the repo root after the data tiers (k8s/data-platform/*) or the governance
contracts change:

    python3 scripts/generate_data_platform_diagram.py
"""

from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots" / "architecture-data-platform.svg"
FONT = "Arial, Helvetica, sans-serif"
W, H = 1740, 800
COLORS = {
    "data": ("#22d3ee", "#06232a"),
    "batch": ("#cddc39", "#20220a"),
    "ai": ("#ffca28", "#2a2008"),
    "governance": ("#b388ff", "#160f2b"),
    "frontend": ("#2dd4bf", "#08211d"),
    "external": ("#8b93a1", "#14161c"),
    "observability": ("#f472b6", "#2a0f1e"),
}
BW, BH = 240, 92
COLX = [40, 330, 620, 910, 1200, 1460]
# (col, y, pillar, title, lines, tech, tier) — tier None = core; else the optional tier label
BOXES = {
    "producers": (0, 150, "external", "EVENT PRODUCERS", ["Tills and web orders (simulator", "plugin shipped by the scenario)"], "Python plugin", None),
    "files": (0, 262, "external", "DAILY FILES", ["Legacy tills + migration files", "with control totals"], "CSV / JSON", None),
    "erp": (0, 374, "external", "ERP DATABASE", ["Operational tables", "(data_sources)"], "PostgreSQL", None),
    "kafka": (1, 150, "data", "KAFKA", ["A topic per tenant:", "tenant-<org>.<topic>"], "Kafka (KRaft)", None),
    "landing": (1, 262, "data", "LANDING BUCKET", ["Raw files, reconciled against", "their control totals"], "SeaweedFS (S3)", None),
    "cdc": (1, 374, "data", "CDC", ["Logical replication of the", "ERP tables into Kafka"], "ai_circus_shared.cdc", None),
    "ingest": (2, 150, "batch", "DATA-INGEST", ["JSON Schema contract + business rules,", "quarantine, idempotent upsert by key,", "hot minute aggregates, lake archiver"], "FastAPI workers", None),
    "bronze": (3, 150, "data", "BRONZE", ["Raw JSONB by key, a schema", "per data product"], "Postgres  data_platform", None),
    "lake": (3, 262, "data", "ICEBERG LAKE", ["One day-partitioned table", "per source"], "PyIceberg on SeaweedFS", None),
    "transform": (4, 150, "batch", "DATA-TRANSFORM", ["dbt: silver (clean, pseudonymised)", "then gold marts, with tests"], "dbt-core · CronJob", None),
    "dagster": (4, 262, "batch", "DAGSTER", ["Sensors on new bronze,", "asset checks, lineage"], "Dagster", "analytics tier"),
    "clickhouse": (4, 374, "data", "CLICKHOUSE MARTS", ["Columnar copy of silver,", "gold rebuilt in place"], "ClickHouse", "analytics tier"),
    "api": (5, 150, "ai", "DATA-API", ["Metrics, records + trail, governed", "SQL, lineage, SSE, simulator"], "FastAPI + sqlglot", None),
    "assistant": (5, 262, "ai", "ASSISTANT", ["Data tools, catalogue tools,", "ml_predict on the exported marts"], "FastAPI + LangChain", None),
    "superset": (5, 374, "frontend", "SUPERSET", ["Dashboards as code with", "row-level security"], "Apache Superset", "bi tier"),
    "openmetadata": (5, 486, "observability", "OPENMETADATA", ["Catalogue, contracts, lineage", "topic → bronze → dbt → dashboard"], "OpenMetadata", "governance tier"),
}
ARROWS = [
    ("producers", "kafka"), ("files", "landing"), ("erp", "cdc"), ("cdc", "kafka"),
    ("kafka", "ingest"), ("landing", "ingest"), ("ingest", "bronze"), ("ingest", "lake"),
    ("bronze", "transform"), ("transform", "dagster"), ("transform", "clickhouse"),
    ("transform", "api"), ("clickhouse", "api"), ("api", "assistant"), ("clickhouse", "superset"),
]
GOV = [
    ("Policy decision point", "llm-gateway asks noop / static / OPA / HTTP; allow, deny, degrade"),
    ("CloudEvents", "content-free events over a log line or an HMAC webhook with an outbox"),
    ("Hash-chained audit", "every admin mutation, verifiable at /governance/audit/verify"),
    ("Decision records", "non-LLM predictions kept with enforced retention"),
    ("AI-BOM", "CycloneDX 1.6 inventory at /governance/inventory"),
]


def box(name: str) -> str:
    col, y, pillar, title, lines, tech, tier = BOXES[name]
    stroke, fill = COLORS[pillar]
    x = COLX[col]
    dash = ' stroke-dasharray="5 4"' if tier else ""
    out = [f'<rect x="{x}" y="{y}" width="{BW}" height="{BH}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.6"{dash}/>']
    out.append(f'<text x="{x + 12}" y="{y + 20}" font-family="{FONT}" font-size="12.5" font-weight="800" letter-spacing="0.8" fill="{stroke}">{escape(title)}</text>')
    for i, line in enumerate(lines):
        out.append(f'<text x="{x + 12}" y="{y + 38 + i * 14}" font-family="{FONT}" font-size="11" fill="#c9d8e6">{escape(line)}</text>')
    out.append(f'<text x="{x + 12}" y="{y + BH - 8}" font-family="{FONT}" font-size="10" fill="#607890">{escape(tech)}</text>')
    if tier:
        out.append(f'<text x="{x + BW - 10}" y="{y + 20}" text-anchor="end" font-family="{FONT}" font-size="9.5" font-style="italic" fill="{stroke}" fill-opacity="0.85">{tier}</text>')
    return "\n".join(out)


def arrow(a: str, b: str) -> str:
    ca, ya, *_ = BOXES[a]
    cb, yb, *_ = BOXES[b]
    x1, y1 = COLX[ca] + BW, ya + BH / 2
    x2, y2 = COLX[cb], yb + BH / 2
    if ca == cb:  # vertical hop inside a column
        x1 = x2 = COLX[ca] + BW / 2
        y1, y2 = (ya + BH, yb) if yb > ya else (ya, yb + BH)
    mx = (x1 + x2) / 2
    path = f"M {x1} {y1} L {x2} {y2}" if x1 == x2 else f"M {x1} {y1} C {mx} {y1}, {mx} {y2}, {x2} {y2}"
    return f'<path d="{path}" fill="none" stroke="#7d8fa3" stroke-opacity="0.7" stroke-width="1.4" marker-end="url(#a)"/>'


def build() -> str:
    gx, gy = 40, 610
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#9fb7cc" opacity="0.85"/></marker></defs>',
        f'<rect width="{W}" height="{H}" fill="#050b16"/>',
        f'<text x="{W / 2}" y="38" text-anchor="middle" font-family="{FONT}" font-size="26" font-weight="800" letter-spacing="2" fill="#f3f8fc">AI OPEN FRAMEWORK</text>',
        f'<text x="{W / 2}" y="58" text-anchor="middle" font-family="{FONT}" font-size="12.5" letter-spacing="0.5" fill="#5c7288">Data products — a governed open-source data platform on the same cluster, switched on tier by tier (off by default)</text>',
    ]
    for col, label in enumerate(["SOURCES", "CAPTURE", "INGEST", "STORE", "TRANSFORM", "SERVE"]):
        p.append(f'<text x="{COLX[col] + BW / 2}" y="118" text-anchor="middle" font-family="{FONT}" font-size="12" font-weight="700" letter-spacing="2" fill="#9fb7cc">{label}</text>')
    p += [arrow(a, b) for a, b in ARROWS]
    p += [box(n) for n in BOXES]
    p.append(f'<rect x="{gx}" y="{gy}" width="1660" height="150" rx="14" fill="#b388ff" fill-opacity="0.04" stroke="#b388ff" stroke-opacity="0.5" stroke-dasharray="3 4"/>')
    p.append(f'<text x="{gx + 16}" y="{gy + 22}" font-family="{FONT}" font-size="12" font-weight="700" letter-spacing="1" fill="#b388ff">TENANCY &amp; GOVERNANCE-READY — transversal, every contract open, every switch off by default</text>')
    p.append(f'<text x="{gx + 16}" y="{gy + 42}" font-family="{FONT}" font-size="11" fill="#c9d8e6">Tenant = Keycloak Organization on every door: topic, bucket prefix, schema, API call. A role then adds row filters and column masks (ai_circus_shared.data_policy).</text>')
    cw = 316
    for i, (t, d) in enumerate(GOV):
        x = gx + 16 + i * (cw + 8)
        p.append(f'<rect x="{x}" y="{gy + 58}" width="{cw}" height="74" rx="8" fill="#160f2b" stroke="#b388ff" stroke-opacity="0.7"/>')
        p.append(f'<text x="{x + 10}" y="{gy + 78}" font-family="{FONT}" font-size="12" font-weight="700" fill="#b388ff">{escape(t)}</text>')
        words, line, ln = d.split(), "", []
        for w in words:
            if len(line) + len(w) > 44:
                ln.append(line)
                line = ""
            line = (line + " " + w).strip()
        ln.append(line)
        for j, s in enumerate(ln[:3]):
            p.append(f'<text x="{x + 10}" y="{gy + 95 + j * 13}" font-family="{FONT}" font-size="10.5" fill="#c9d8e6">{escape(s)}</text>')
    p.append(f'<text x="{W / 2}" y="{H - 14}" text-anchor="middle" font-family="{FONT}" font-size="10.5" fill="#5c7288">Dashed border = optional tier (analytics · bi · governance). Core = Kafka, data-ingest, data-transform, data-api. See architecture-detailed.svg for the rest of the platform.</text>')
    p.append("</svg>")
    return "\n".join(p) + "\n"


if __name__ == "__main__":
    OUT.write_text(build())
    print(f"wrote {OUT}")
