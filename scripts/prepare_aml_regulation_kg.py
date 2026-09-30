#!/usr/bin/env python3
"""Corpus + knowledge-graph preparation for the `aml_regulation_kg` scenario.

One public source, pinned by SHA-256 and cached under `~/.cache/ai-circus/amlr/`: the
English XHTML of **Regulation (EU) 2024/1624** (the EU Anti-Money Laundering Regulation,
"AMLR", CELEX 32024R1624) from the Publications Office's Cellar — the same document
EUR-Lex serves, without EUR-Lex's bot challenge. Reuse is authorised with attribution
(Commission Decision 2011/833/EU; "© European Union").

Two steps, run separately on purpose:

- `fetch` (free, deterministic): cuts the `ARTICLES` below out of the regulation into
  `scenarios/aml_regulation_kg/sample_docs/amlr-art-NNN-<slug>.md` — the documents
  etl-vectorize chunks and embeds, and the text every graph edge must quote.
- `extract` (spends LLM tokens, run once): asks an LLM through llm-gateway to read each
  article against a closed ontology (`CLASSES`, `RELATIONS`) and return entities plus
  relations, each relation with the article paragraph and a *verbatim* quote. A relation
  whose quote is not literally in the article is dropped (a deterministic guard against
  invented law). Answers are cached per article, so a re-run spends no tokens; the result
  is written to `scenarios/aml_regulation_kg/knowledge_graph.json` in the
  `ai_circus_shared.network_graph` contract and committed after a human review — the
  cluster pipeline never calls an LLM to build the graph.

Article nodes are added by the script, not the LLM: every concept named in a relation is
linked `cited_in` to its article (HippoRAG 2's "passage nodes"), which is how rag-agent's
Personalized PageRank reaches the article text.

Run (needs the cluster's llm-gateway on localhost:4000 for `extract`, e.g.
`kubectl -n ai-circus port-forward svc/llm-gateway 4000:4000`, and its key in
`LLM_GATEWAY_API_KEY`):
`cd services/rag-agent && uv run python ../../scripts/prepare_aml_regulation_kg.py fetch|extract [--model groq-llama]`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import time
import unicodedata
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import httpx
from ai_circus_shared.network_graph import GraphEdge, GraphNode, NetworkGraph

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "scenarios/aml_regulation_kg"
DOCS_DIR = SCENARIO_DIR / "sample_docs"
GRAPH_PATH = SCENARIO_DIR / "knowledge_graph.json"
CACHE_DIR = Path.home() / ".cache/ai-circus/amlr"

SOURCE_URL = "http://publications.europa.eu/resource/cellar/868bf3cf-2dd4-11ef-a61b-01aa75ed71a1.0006.03/DOC_1"
SOURCE_SHA256 = "83a2e2e277276cc26b2b0a980382e38f2beee4b6c06446710e8bf2d4f56f3512"
ATTRIBUTION = (
    "Regulation (EU) 2024/1624 of the European Parliament and of the Council of 31 May 2024 (AMLR), "
    "OJ L, 2024/1624, 19.6.2024. Source: EUR-Lex — © European Union, reuse authorised (Decision 2011/833/EU)."
)

# A compliance officer's path through the regulation: who is obliged, knowing the
# customer, the risk-based escalations, and what to do once something looks suspicious.
ARTICLES = [3, 11, 19, 20, 21, 26, 34, 39, 42, 45, 46, 52, 69, 71, 73, 77, 79, 80]

CLASSES = {
    "ObligedEntity": "a kind of firm or professional the regulation obliges (e.g. credit institution, notary)",
    "Obligation": "something an obliged entity must do or must not do, phrased as an action",
    "Authority": "a public body (e.g. FIU, supervisor, AMLA, Commission)",
    "Party": "a person or entity the obligations concern (customer, beneficial owner, PEP, shell institution)",
    "Trigger": "a condition or event that makes an obligation apply (suspicion, high-risk third country)",
    "Record": "information or a document that must be obtained, kept or provided",
    "Limit": "an amount, percentage or time limit (e.g. EUR 10 000, 25 %, five years)",
}
RELATIONS = {
    "must_perform": "ObligedEntity -> Obligation it must carry out",
    "must_not": "ObligedEntity -> Obligation it is prohibited from (label the prohibited act)",
    "may": "ObligedEntity|Authority -> Obligation it is allowed or empowered to carry out",
    "triggered_by": "Obligation -> Trigger that makes it apply",
    "applies_to": "Obligation -> Party it concerns",
    "reports_to": "Obligation -> Authority that receives it",
    "requires": "Obligation -> Obligation|Record it consists of or needs",
    "has_limit": "Obligation|Trigger -> Limit that bounds it",
    "is_a": "X -> more general X of the same class (e.g. Credit institution is_a Obliged entity)",
}
# The (source, target) class each relation implies, where it implies one.
SIGNATURES: dict[str, tuple[str | None, str | None]] = {
    "must_perform": ("ObligedEntity", "Obligation"),
    "must_not": ("ObligedEntity", "Obligation"),
    "may": (None, "Obligation"),
    "triggered_by": ("Obligation", "Trigger"),
    "applies_to": ("Obligation", "Party"),
    "reports_to": ("Obligation", "Authority"),
    "requires": ("Obligation", None),
    "has_limit": (None, "Limit"),
}
MAX_RELATIONS_PER_ARTICLE = 14


# --- fetch -------------------------------------------------------------------------


def fetch_source() -> str:
    """Download the regulation once, then refuse anything whose SHA-256 differs from the reviewed one."""
    path = CACHE_DIR / "amlr-32024R1624.xhtml"
    if not path.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {SOURCE_URL} …")
        partial = path.with_suffix(".part")
        request = urllib.request.Request(SOURCE_URL, headers={"Accept": "application/xhtml+xml"})
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:  # ruff: ignore[suspicious-url-open-usage]
            shutil.copyfileobj(response, out)
        partial.rename(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCE_SHA256:
        raise SystemExit(f"{path} has SHA-256 {digest}, expected {SOURCE_SHA256} — refusing to use it.")
    return data.decode("utf-8")


class _ArticleText(HTMLParser):
    """Flattens one article's XHTML into paragraphs: each `<p>` is a block, except that
    the cells of one table row — the regulation's "(a) | text" list items — join into one line."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self._row: list[str] | None = None
        self._text: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "p":
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._text is not None:
            text = " ".join("".join(self._text).split())
            if text:
                (self._row if self._row is not None else self.blocks).append(text)
            self._text = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.blocks.append("   " + " ".join(self._row))
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)


def slugify(text: str) -> str:
    """Lower-case ASCII words joined by '-'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def split_articles(xhtml: str) -> dict[int, tuple[str, list[str]]]:
    """{article number: (title, paragraphs)} for the selected ARTICLES."""
    starts = [(int(m.group(1)), m.start()) for m in re.finditer(r'<div class="eli-subdivision" id="art_(\d+)">', xhtml)]
    articles: dict[int, tuple[str, list[str]]] = {}
    for i, (number, start) in enumerate(starts):
        if number not in ARTICLES:
            continue
        end = starts[i + 1][1] if i + 1 < len(starts) else len(xhtml)
        segment = xhtml[start:end]
        title_match = re.search(r'class="oj-sti-art">(.*?)</p>', segment, re.S)
        title = " ".join(re.sub(r"<[^>]+>", "", title_match.group(1)).split()) if title_match else f"Article {number}"
        parser = _ArticleText()
        parser.feed(segment)
        # Drop the "Article N" heading and the title — the markdown header carries both.
        paragraphs = [b for b in parser.blocks if b not in (f"Article {number}", title)]
        articles[number] = (title, paragraphs)
    missing = set(ARTICLES) - set(articles)
    if missing:
        raise SystemExit(f"Articles not found in the source: {sorted(missing)}")
    return articles


def doc_name(number: int, title: str) -> str:
    """The markdown file (and the article node's citation) for one article."""
    words, slug = slugify(title).split("-"), ""
    for word in words:  # whole words, at most ~48 characters
        if slug and len(slug) + len(word) > 48:
            break
        slug = f"{slug}-{word}" if slug else word
    return f"amlr-art-{number:03d}-{slug}.md"


def write_docs(articles: dict[int, tuple[str, list[str]]]) -> None:
    """(Re)write sample_docs/ from the selected articles."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for old in DOCS_DIR.glob("amlr-art-*.md"):
        old.unlink()
    for number, (title, paragraphs) in sorted(articles.items()):
        body = "\n\n".join(paragraphs)
        text = f"# AMLR Article {number} — {title}\n\n_{ATTRIBUTION}_\n\n{body}\n"
        (DOCS_DIR / doc_name(number, title)).write_text(text, encoding="utf-8")
    words = sum(len(" ".join(p).split()) for _, p in articles.values())
    print(f"Wrote {len(articles)} articles ({words:,} words) to {DOCS_DIR.relative_to(ROOT)}")


# --- extract -----------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Case-, quote-, dash- and whitespace-insensitive form used to check quotes."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-―]", "-", text)
    return " ".join(text.split())


def quote_in_text(quote: str, normalised_text: str) -> bool:
    """True if `quote` is verbatim in the text — an elided quote ("a ... b") counts when
    every fragment is, in order, and together they still carry at least 5 words."""
    fragments = [_normalise(f).strip(" ,;:.") for f in re.split(r"\.\.\.|…", quote)]
    fragments = [f for f in fragments if f]
    if sum(len(f.split()) for f in fragments) < 5:
        return False
    position = 0
    for fragment in fragments:
        found = normalised_text.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True


def _prompt(number: int, title: str, body: str, known: dict[str, list[str]]) -> str:
    classes = "\n".join(f"- {name}: {desc}" for name, desc in CLASSES.items())
    relations = "\n".join(f"- {name}: {desc}" for name, desc in RELATIONS.items())
    known_lines = "\n".join(f"- {cls}: {', '.join(labels)}" for cls, labels in known.items() if labels) or "(none yet)"
    return f"""Build a small knowledge graph from ONE article of Regulation (EU) 2024/1624 (the EU
Anti-Money Laundering Regulation, "AMLR") for a bank compliance officer.

Entity classes:
{classes}

Relation types (direction source -> target):
{relations}

Entities already in the graph — reuse the EXACT label when you mean the same concept:
{known_lines}

Rules:
- At most {MAX_RELATIONS_PER_ARTICLE} relations: the ones a compliance officer would act on.
- Labels: short (2-7 words), sentence case, generic (e.g. "Report suspicion to the FIU", "Obliged entity").
- Every relation has "paragraph" (e.g. "1", "3(b)", "" if unnumbered) and "evidence": a VERBATIM quote
  of 5-30 consecutive words copied from the article text that supports it.
- Only facts stated in this article. No relation without evidence.

Return JSON only, relations first (never an empty list), then every entity they name:
{{"relations": [{{"source": str (label), "relation": str, "target": str (label), "paragraph": str, "evidence": str}}],
  "entities": [{{"label": str, "class": str, "description": str (one sentence)}}]}}

Article {number} — {title}
<article>
{body}
</article>"""


def call_llm(prompt: str, model: str) -> tuple[dict[str, object], int]:
    """One JSON-mode chat completion through llm-gateway; returns (parsed JSON, tokens used)."""
    base_url = os.environ.get("LLM_GATEWAY_URL", "http://localhost:4000")
    api_key = os.environ.get("LLM_GATEWAY_API_KEY")
    if not api_key:
        raise SystemExit("Set LLM_GATEWAY_API_KEY (llm-gateway's LITELLM_MASTER_KEY) to run `extract`.")
    for attempt in range(8):
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "temperature": 0 if attempt == 0 else 0.3,  # a failed JSON generation retries sampled
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=180,
        )
        if "json_validate_failed" in response.text:
            print(f"  invalid JSON generated, retrying ({attempt + 1}/8)")
            continue
        if response.status_code != 429:  # free tiers cap tokens per minute: wait it out
            break
        print(f"  rate-limited, retrying in 30 s ({attempt + 1}/8)")
        time.sleep(30)
    if response.is_error:
        raise SystemExit(f"llm-gateway answered {response.status_code}: {response.text[:600]}")
    payload = response.json()
    return json.loads(payload["choices"][0]["message"]["content"]), int(payload.get("usage", {}).get("total_tokens", 0))


def legal_reference(paragraph: str) -> str:
    """The paragraph part of a citation, as lawyers write it: "1(d)" -> "(1)(d)",
    "3" -> "(3)", "(a)" stays, "" -> ""."""
    paragraph = paragraph.replace(" ", "")
    match = re.fullmatch(r"(\d+)((?:\([^()]+\))*)", paragraph)
    if match:
        return f"({match.group(1)}){match.group(2)}"
    return paragraph if paragraph.startswith("(") else (f"({paragraph})" if paragraph else "")


def node_id(cls: str, label: str) -> str:
    """Deterministic id, so the same concept extracted from two articles is one node."""
    return f"{cls.lower()}:{slugify(label)}"[:200]


def extract(model: str) -> None:
    """Build knowledge_graph.json from the cached/fresh per-article LLM answers."""
    articles = split_articles(fetch_source())
    cache = CACHE_DIR / "extract" / slugify(model)
    cache.mkdir(parents=True, exist_ok=True)

    nodes: dict[str, GraphNode] = {}
    label_class: dict[str, str] = {}  # label (lower) -> class, for relation endpoints
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    tokens = 0
    dropped: list[str] = []

    for number, (title, paragraphs) in sorted(articles.items()):
        body = "\n\n".join(paragraphs)
        cached = cache / f"art-{number:03d}.json"
        if cached.exists():
            answer = json.loads(cached.read_text())
        else:
            known: dict[str, list[str]] = {cls: [] for cls in CLASSES}
            for node in nodes.values():
                if node.type in known and node.label:
                    known[node.type].append(node.label)
            answer, used = call_llm(_prompt(number, title, body, known), model)
            if not answer.get("relations"):  # entities alone happen now and then: ask once more
                answer, retry_used = call_llm(_prompt(number, title, body, known), model)
                used += retry_used
            tokens += used
            cached.write_text(json.dumps(answer, indent=1, ensure_ascii=False))
            print(f"Art. {number}: {used:,} tokens")

        article = f"article:{number}"
        nodes[article] = GraphNode(
            id=article,
            kind="entity",
            type="Article",
            label=f"Art. {number} — {title}"[:200],
            description=title,
            citation=doc_name(number, title),
        )
        for entity in answer.get("entities", []):  # type: ignore[union-attr]
            cls, label = str(entity.get("class", "")), str(entity.get("label", "")).strip()
            if cls not in CLASSES or not label:
                dropped.append(f"Art. {number} entity {label!r} ({cls})")
                continue
            nid = node_id(cls, label)
            label_class.setdefault(label.lower(), cls)
            if nid not in nodes:
                nodes[nid] = GraphNode(
                    id=nid,
                    kind="entity",
                    type=cls,
                    label=label[:200],
                    description=str(entity.get("description", ""))[:2000] or None,
                )

        normalised_body = _normalise(body)
        for rel in answer.get("relations", [])[:MAX_RELATIONS_PER_ARTICLE]:  # type: ignore[union-attr]
            kind, evidence = str(rel.get("relation", "")), str(rel.get("evidence", "")).strip()
            src_label, dst_label = str(rel.get("source", "")).strip(), str(rel.get("target", "")).strip()
            # An endpoint the answer forgot to list as an entity takes the class the
            # relation's signature implies (e.g. whatever an entity `must_perform` is an Obligation).
            src_sig, dst_sig = SIGNATURES.get(kind, (None, None))
            src_cls = label_class.get(src_label.lower()) or src_sig
            dst_cls = label_class.get(dst_label.lower()) or dst_sig
            for label, cls in ((src_label, src_cls), (dst_label, dst_cls)):
                if cls is not None and label and node_id(cls, label) not in nodes:
                    label_class.setdefault(label.lower(), cls)
                    nodes[node_id(cls, label)] = GraphNode(
                        id=node_id(cls, label), kind="entity", type=cls, label=label[:200]
                    )
            reason = (
                "unknown relation"
                if kind not in RELATIONS
                else "unknown endpoint"
                if src_cls is None or dst_cls is None
                else "quote not in article"
                if not quote_in_text(evidence, normalised_body)
                else None
            )
            if reason:
                dropped.append(f"Art. {number} {src_label!r} -{kind}-> {dst_label!r}: {reason}")
                continue
            assert src_cls is not None and dst_cls is not None
            src, dst = node_id(src_cls, src_label), node_id(dst_cls, dst_label)
            if src == dst:
                continue
            paragraph = str(rel.get("paragraph", "")).strip()
            citation = f"AMLR Art. {number}{legal_reference(paragraph)}"
            edges.setdefault(
                (src, kind, dst),
                GraphEdge(
                    source=src,
                    target=dst,
                    kind=kind,
                    label=kind.replace("_", " "),
                    citation=citation[:500],
                    evidence=evidence[:1000],
                ),
            )
            for concept in (src, dst):
                edges.setdefault(
                    (concept, "cited_in", article),
                    GraphEdge(source=concept, target=article, kind="cited_in", label="cited in"),
                )

    # Keep only concepts some relation uses — an isolated entity is noise to PageRank and to the eye.
    used = {e.source for e in edges.values()} | {e.target for e in edges.values()}
    graph = NetworkGraph(nodes=[n for n in nodes.values() if n.id in used], edges=list(edges.values()))
    GRAPH_PATH.write_text(
        json.dumps(graph.model_dump(exclude_none=True, exclude_defaults=True), indent=1, ensure_ascii=False) + "\n"
    )

    by_class: dict[str, int] = {}
    for n in graph.nodes:
        by_class[n.type or "?"] = by_class.get(n.type or "?", 0) + 1
    semantic = sum(1 for e in graph.edges if e.kind != "cited_in")
    print(f"Tokens spent this run: {tokens:,} (cached articles cost nothing)")
    print(
        f"Graph: {len(graph.nodes)} nodes {by_class}, {semantic} relations + {len(graph.edges) - semantic} cited_in links"
    )
    print(f"Dropped {len(dropped)} item(s):")
    for line in dropped:
        print("  " + line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=["fetch", "extract"])
    parser.add_argument("--model", default="groq-llama", help="llm-gateway model_name used by `extract`")
    args = parser.parse_args()
    if args.step == "fetch":
        write_docs(split_articles(fetch_source()))
    else:
        extract(args.model)


if __name__ == "__main__":
    main()
