#!/usr/bin/env python3
"""Real legal texts for the `prestaciones_sociales` assistant's regulations tool.

`prestaciones_sociales` (a FICTIONAL town's emergency social aid) lets its assistant
search the rules an application is judged by (`documents.tool: consultar_normativa`).
Two kinds of documents live in `scenarios/prestaciones_sociales/sample_docs/`:

- **Real, verbatim** — written by this script: the articles of Spanish state law the
  procedure, the anti-fraud checks and the anti-favouritism duties rest on, from the
  BOE's consolidated-legislation open-data API (current version of each article), plus
  the EU AI Act's human-oversight and right-to-explanation articles and the Annex III
  point on public assistance, from the Publications Office's Cellar (Spanish XHTML,
  pinned by SHA-256). Legal texts carry no copyright in Spain (art. 13 LPI); EU texts
  are reusable with attribution (Decision 2011/833/EU).
- **Fictional** — hand-written and committed, never touched by this script: Villaclara's
  *bases reguladoras* of the aid, an internal instruction on fraud red flags and on
  departing from the system's proposal, and a citizen-service protocol. They must stay
  consistent with the scenario's `business_rules` (libs/shared tests check every
  `legal_basis` resolves to a document).

Each file starts with `# <Norma> · <Artículo>` and a `> ` provenance line — assistant's
`GET /documents/{slug}` reads both for the UI's reading list. Downloads are cached under
`~/.cache/ai-circus/prestaciones_normativa/`.

Run: `uv run --with httpx python scripts/prepare_prestaciones_normativa.py`
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "scenarios/prestaciones_sociales/sample_docs"
CACHE_DIR = Path.home() / ".cache/ai-circus/prestaciones_normativa"

BOE_API = "https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{law}/texto/bloque/{block}"
BOE_PAGE = "https://www.boe.es/buscar/act.php?id={law}#{block}"
COPYRIGHT_NOTE = "Texto oficial, sin derechos de autor (art. 13 de la Ley de Propiedad Intelectual)."

# (BOE id, short name, full title, file prefix, articles): why each one is here is in the
# scenario doc — procedure (39/2015), favouritism (40/2015), fraud and refunds (38/2003),
# municipal competence (7/1985).
BOE_LAWS = [
    (
        "BOE-A-2015-10565",
        "Ley 39/2015",
        "Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas",
        "ley-39-2015",
        [21, 28, 35, 53, 66, 68, 69],
    ),
    (
        "BOE-A-2015-10566",
        "Ley 40/2015",
        "Ley 40/2015, de 1 de octubre, de Régimen Jurídico del Sector Público",
        "ley-40-2015",
        [23, 24],
    ),
    (
        "BOE-A-2003-20977",
        "Ley 38/2003",
        "Ley 38/2003, de 17 de noviembre, General de Subvenciones",
        "ley-38-2003",
        [14, 37],
    ),
    (
        "BOE-A-1985-5392",
        "Ley 7/1985",
        "Ley 7/1985, de 2 de abril, Reguladora de las Bases del Régimen Local",
        "ley-7-1985",
        [25],
    ),
]

AI_ACT_URL = "http://publications.europa.eu/resource/cellar/dc8116a1-3fe6-11ef-865a-01aa75ed71a1.0023.03/DOC_1"
AI_ACT_SHA256 = "0ef11d381226cde823d0bbf03ed195ec98645b9a0b7e06b1668c7f46751aad8c"
AI_ACT_NOTE = (
    "Fuente: Reglamento (UE) 2024/1689 del Parlamento Europeo y del Consejo, de 13 de junio de 2024 "
    "(Reglamento de Inteligencia Artificial), DO L, 2024/1689, 12.7.2024 — EUR-Lex, © Unión Europea, "
    "reutilización autorizada (Decisión 2011/833/UE)."
)
AI_ACT_ARTICLES = [14, 86]


def slugify(text: str, max_words: int = 7) -> str:
    """Lower-case ASCII words joined by '-', at most `max_words`."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "-".join(re.findall(r"[a-z0-9]+", ascii_text)[:max_words])


def cached_get(url: str, name: str, headers: dict[str, str]) -> bytes:
    """GET once, then serve from the cache."""
    path = CACHE_DIR / name
    if not path.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {url} …")
        response = httpx.get(url, headers=headers, timeout=120, follow_redirects=True)
        response.raise_for_status()
        path.write_bytes(response.content)
    return path.read_bytes()


def _clean(fragment: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", "", fragment)).split())


def boe_article(law: str, number: int) -> tuple[str, list[str]]:
    """(heading, paragraphs) of the article's current consolidated version."""
    xml = cached_get(
        BOE_API.format(law=law, block=f"a{number}"), f"{law}-a{number}.xml", {"Accept": "application/xml"}
    ).decode("utf-8")
    versions = re.findall(r"<version\b.*?</version>", xml, re.S)
    if not versions:
        raise SystemExit(f"{law} a{number}: no version in the BOE response.")
    paragraphs = [
        (cls, _clean(body))
        for cls, body in re.findall(r'<p class="([^"]*)">(.*?)</p>', versions[-1], re.S)
        if not cls.startswith("nota")
    ]
    heading = next((text for cls, text in paragraphs if cls == "articulo"), f"Artículo {number}").rstrip(".")
    return heading, [text for cls, text in paragraphs if cls != "articulo" and text]


class _Blocks(HTMLParser):
    """Flattens EU XHTML into paragraphs; the cells of one table row (the "a) | text"
    list items) join into one line — same approach as prepare_aml_regulation_kg.py."""

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
                self.blocks.append(" ".join(self._row))
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)


def _blocks(segment: str) -> list[str]:
    parser = _Blocks()
    parser.feed(segment)
    return parser.blocks


def ai_act_documents() -> dict[str, str]:
    """Articles 14 and 86 and Annex III point 5 of the AI Act, in Spanish."""
    data = cached_get(AI_ACT_URL, "ai-act-32024R1689-es.xhtml", {"Accept": "application/xhtml+xml"})
    digest = hashlib.sha256(data).hexdigest()
    if digest != AI_ACT_SHA256:
        raise SystemExit(f"AI Act source has SHA-256 {digest}, expected {AI_ACT_SHA256} — refusing to use it.")
    xhtml = data.decode("utf-8")
    docs: dict[str, str] = {}
    for number in AI_ACT_ARTICLES:
        start = xhtml.index(f'id="art_{number}"')
        end = xhtml.index(f'id="art_{number + 1}"')
        blocks = _blocks(xhtml[start:end])
        title = blocks[1]  # blocks[0] is "Artículo N"
        heading = f"Artículo {number}. {title}"
        name = f"reglamento-ia-2024-1689-art-{number:03d}-{slugify(title)}.md"
        docs[name] = _markdown(f"Reglamento (UE) 2024/1689 · {heading}", AI_ACT_NOTE, blocks[2:])
    # The annex nests each point's list inside its row, which the row-joining parser
    # flattens away — cut point 5 out of the plain text and split it on its items.
    start, end = xhtml.index('id="anx_III"'), xhtml.index('id="anx_IV"')
    annex = _clean(xhtml[start:end])
    intro = annex[annex.index("Los sistemas de IA de alto riesgo") : annex.index("1. Biometría")].strip()
    point = annex[annex.index("5. Acceso a servicios") : annex.index("6. Garantía del cumplimiento")].strip()
    docs["reglamento-ia-2024-1689-anexo-iii-punto-5-prestaciones-publicas.md"] = _markdown(
        "Reglamento (UE) 2024/1689 · Anexo III, punto 5. Sistemas de IA de alto riesgo: servicios y prestaciones "
        "esenciales",
        AI_ACT_NOTE,
        [intro, *re.split(r" (?=[a-d]\) Sistemas de IA)", point)],
    )
    return docs


def _markdown(heading: str, note: str, paragraphs: list[str]) -> str:
    return f"# {heading}\n\n> {note}\n\n" + "\n\n".join(paragraphs) + "\n"


def boe_documents() -> dict[str, str]:
    docs: dict[str, str] = {}
    for law, short, title, prefix, articles in BOE_LAWS:
        for number in articles:
            heading, paragraphs = boe_article(law, number)
            subject = heading.split(".", 1)[1].strip() if "." in heading else ""
            if not subject:  # e.g. LBRL art. 25 has no title
                subject = "Competencias propias del municipio"
                heading = f"Artículo {number}. {subject}"
            name = f"{prefix}-art-{number:03d}-{slugify(subject)}.md"
            note = (
                f"Fuente: {title} ({law}), texto consolidado vigente — "
                f"{BOE_PAGE.format(law=law, block=f'a{number}')}. {COPYRIGHT_NOTE}"
            )
            docs[name] = _markdown(f"{short} · {heading}", note, paragraphs)
    return docs


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    docs = {**boe_documents(), **ai_act_documents()}
    for name, text in sorted(docs.items()):
        (DOCS_DIR / name).write_text(text, encoding="utf-8")
        print(f"  {name} ({len(text):,} chars)")
    print(f"Wrote {len(docs)} real legal texts to {DOCS_DIR.relative_to(ROOT)}.")


if __name__ == "__main__":
    main()
