"""
- Title:    Weekly bulletin as a PDF (a small markdown subset rendered with reportlab)
- Author:   Angel Martinez-Tenor

The bulletin is generated as markdown by `ai_circus_shared.drought_bulletin`: headings, one table, bullets, bold and
plain paragraphs, nothing else. This renders exactly that subset, so the PDF and the text can never carry different
figures. Standard PDF fonts cover English and Spanish text.
"""

from __future__ import annotations

import re
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    return _BOLD.sub(r"<b>\1</b>", escape(text))


def render(markdown: str, *, title: str) -> bytes:
    """The bulletin markdown as PDF bytes."""
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10)
    cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=10)
    quote = ParagraphStyle(
        "quote", parent=body, textColor=colors.HexColor("#8a4b00"), backColor=colors.HexColor("#fff4e5")
    )
    story: list = []
    table_rows: list[list[str]] = []
    bullets: list[str] = []

    def flush_table() -> None:
        if not table_rows:
            return
        data = [[Paragraph(_inline(c), cell) for c in row] for row in table_rows]
        t = Table(data, repeatRows=1)
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef4")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b8c4cf")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])
        )
        story.extend([t, Spacer(1, 6)])
        table_rows.clear()

    def flush_bullets() -> None:
        if bullets:
            items = [ListItem(Paragraph(_inline(b), body), leftIndent=12) for b in bullets]
            story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=12))
            story.append(Spacer(1, 4))
            bullets.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            flush_bullets()
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(set(c) <= {"-", ":", " "} for c in cells):
                table_rows.append(cells)
            continue
        flush_table()
        if line.startswith("- "):
            bullets.append(line[2:])
            continue
        flush_bullets()
        if line.startswith("# "):
            story.append(Paragraph(_inline(line[2:]), styles["Title"]))
        elif line.startswith("## "):
            story.extend([Spacer(1, 6), Paragraph(_inline(line[3:]), styles["Heading2"])])
        elif line.startswith("> "):
            story.extend([Paragraph(_inline(line[2:]), quote), Spacer(1, 6)])
        elif line.strip() == "---":
            story.append(HRFlowable(width="100%", color=colors.HexColor("#b8c4cf")))
        elif line.strip():
            story.append(
                Paragraph(
                    _inline(line), small if line.startswith("**Sources") or line.startswith("**Fuentes") else body
                )
            )
    flush_table()
    flush_bullets()

    buffer = BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, title=title, author="Riverbend Water Authority (fictional)", leftMargin=1.8 * cm,
        rightMargin=1.8 * cm, topMargin=1.6 * cm, bottomMargin=1.6 * cm,
    ).build(story)  # fmt: skip
    return buffer.getvalue()
