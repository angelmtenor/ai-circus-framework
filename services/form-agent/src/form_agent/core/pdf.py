"""
- Title:    Filled-form PDF (draft and filed copy with its receipt)
- Author:   Angel Martinez-Tenor

Renders any `assisted_form` scenario's form as an official-looking A4 document, straight
from its `FormConfig` — the same data ui-react's sheet renders, so no per-scenario
template file exists: numbered section bands, boxes on a 12-column grid with their
printed box numbers ("casillas"), ticked checkboxes drawn as an X, amounts and dates
in the form's locale. A form without `sections` (a plain intake form) prints as one
section with every field in it.

Two flavours: a *draft* (diagonal "BORRADOR / DRAFT" watermark, from whatever the user
has filled so far) and the *filed copy* of a persisted submission — the registry stamp
in the "reserved for the Administration" box, the signature line, and a final receipt
page ("justificante") with the registration number, timestamp, a verification code, a
QR code and a Code 128 barcode.

Only reportlab's built-in fonts are used (no font files in the image): Helvetica
covers Windows-1252, i.e. every Spanish/Western character plus "€"; anything outside it
is replaced by "?" rather than drawn as a missing-glyph box.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ai_circus_shared.form_validation import is_checked
from ai_circus_shared.scenario_schema import FormConfig, FormFieldSpec, FormSection
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128, qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = A4
MARGIN = 34.0
CONTENT_W = PAGE_W - 2 * MARGIN
COLUMN_W = CONTENT_W / 12
FOOTER_H = 30.0

INK = HexColor("#0b1f33")
PRIMARY = HexColor("#1d4e6f")
TINT = HexColor("#e6eef4")
RULE = HexColor("#5b6b7a")
MUTED = HexColor("#56687a")
STAMP = HexColor("#b3261e")
WATERMARK = Color(0.11, 0.30, 0.44, alpha=0.08)

WORDS: dict[str, dict[str, str]] = {
    "es": {
        "copy": "Ejemplar para el interesado",
        "draft": "BORRADOR · SIN VALIDEZ",
        "page": "Página {n} de {total}",
        "reserved": "Espacio reservado para la Administración",
        "registry": "REGISTRO ELECTRÓNICO",
        "entry": "ENTRADA",
        "no": "Nº",
        "signed": "Firmado electrónicamente por {who} el {when}",
        "unsigned": "Pendiente de firma electrónica",
        "receipt": "Justificante de presentación",
        "reg_no": "Número de registro",
        "submitted_at": "Fecha y hora de presentación",
        "applicant": "Interesado",
        "model": "Modelo",
        "issuer": "Órgano",
        "csv": "Código seguro de verificación (CSV)",
        "verify": "Verifique la autenticidad de este documento con el CSV en la sede electrónica.",
        "demo": "Documento de demostración generado automáticamente: sin validez administrativa.",
        "boxes": "{filled} casillas cumplimentadas",
    },
    "en": {
        "copy": "Applicant's copy",
        "draft": "DRAFT · NOT VALID",
        "page": "Page {n} of {total}",
        "reserved": "Reserved for the Administration",
        "registry": "ELECTRONIC REGISTRY",
        "entry": "RECEIVED",
        "no": "No.",
        "signed": "Electronically signed by {who} on {when}",
        "unsigned": "Awaiting electronic signature",
        "receipt": "Filing receipt",
        "reg_no": "Registration number",
        "submitted_at": "Submitted at",
        "applicant": "Applicant",
        "model": "Form",
        "issuer": "Issued by",
        "csv": "Verification code",
        "verify": "Check this document's authenticity with its verification code.",
        "demo": "Automatically generated demo document: no legal validity.",
        "boxes": "{filled} boxes filled in",
    },
}

_DEFAULT_SPAN = {"textarea": 12, "checkbox": 6, "date": 3, "number": 3, "currency": 4}


@dataclass(frozen=True)
class Filing:
    """What turns a draft into the filed copy: the submission's registry data."""

    case_number: str
    submitted_at: datetime
    verification_code: str


def _latin(text: str) -> str:
    """Helvetica (built-in, Windows-1252): replace what it can't draw instead of boxes."""
    return text.encode("cp1252", errors="replace").decode("cp1252")


def format_value(spec: FormFieldSpec, value: str, locale: str) -> str:
    """A box's printed value: localized amounts/dates, option text as-is."""
    value = value.strip()
    if not value:
        return ""
    if spec.type == "currency":
        try:
            amount = float(value.replace(",", "."))
        except ValueError:
            return value
        text = f"{amount:,.2f}"
        if locale == "es":
            text = text.replace(",", "·").replace(".", ",").replace("·", ".")
        return f"{text} €"
    if spec.type == "date":
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return value
        return parsed.strftime("%d/%m/%Y" if locale == "es" else "%Y-%m-%d")
    return value


def _span(spec: FormFieldSpec) -> int:
    return spec.span or _DEFAULT_SPAN.get(spec.type, 6)


def _ordered_sections(form: FormConfig, values: dict[str, str]) -> list[tuple[FormSection, list[FormFieldSpec]]]:
    """(section, its active fields) in print order: shared "before", the selected
    variant's own, shared "after". A plain form is one section named after it."""
    active = form.active_fields(values)
    if not form.sections:
        return [(FormSection(id="_all", title=form.title), active)]
    variant = form.variant_for(values)
    sections = [
        *(s for s in form.sections if s.placement == "before"),
        *(variant.sections if variant else []),
        *(s for s in form.sections if s.placement == "after"),
    ]
    by_section: dict[str, list[FormFieldSpec]] = {s.id: [] for s in sections}
    for spec in active:
        if spec.id != form.classification_field and spec.section in by_section:
            by_section[spec.section].append(spec)
    return [(s, by_section[s.id]) for s in sections if by_section[s.id]]


def _rows(fields: list[FormFieldSpec]) -> list[list[tuple[FormFieldSpec, int]]]:
    """Pack fields into 12-column rows; the last box of a row stretches to fill it."""
    rows: list[list[tuple[FormFieldSpec, int]]] = []
    current: list[tuple[FormFieldSpec, int]] = []
    used = 0
    for spec in fields:
        span = _span(spec)
        if current and used + span > 12:
            rows.append(current)
            current, used = [], 0
        current.append((spec, span))
        used += span
    if current:
        rows.append(current)
    for row in rows:
        spec, span = row[-1]
        row[-1] = (spec, span + 12 - sum(s for _, s in row))
    return rows


def applicant_name(form: FormConfig, values: dict[str, str]) -> str:
    """The applicant as printed on the receipt/signature — `applicant_fields` joined."""
    return " ".join(values.get(f, "").strip() for f in form.applicant_fields if values.get(f, "").strip())


def _initials(name: str) -> str:
    small = {"de", "del", "la", "las", "los", "y", "of", "the", "and"}
    return "".join(word[0] for word in name.split() if word.lower() not in small)[:3].upper()


class _NumberedCanvas(canvas.Canvas):
    """Defers each page so the footer can say "page n of total"."""

    def __init__(self, *args: Any, footer: tuple[str, str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved: list[dict[str, Any]] = []
        self._footer = footer

    def showPage(self) -> None:  # noqa: N802 — reportlab API name
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        self._saved.append(dict(self.__dict__))  # the page still being drawn
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total: int) -> None:
        left, page_template = self._footer
        self.setStrokeColor(RULE)
        self.setLineWidth(0.4)
        self.line(MARGIN, FOOTER_H - 6, PAGE_W - MARGIN, FOOTER_H - 6)
        self.setFont("Helvetica", 6.5)
        self.setFillColor(MUTED)
        self.drawString(MARGIN, FOOTER_H - 16, _latin(left))
        self.drawRightString(
            PAGE_W - MARGIN, FOOTER_H - 16, _latin(page_template.format(n=self._pageNumber, total=total))
        )


class _Renderer:
    def __init__(self, form: FormConfig, values: dict[str, str], filing: Filing | None) -> None:
        self.form = form
        self.values = values
        self.filing = filing
        self.words = WORDS.get(form.locale, WORDS["en"])
        self.variant = form.variant_for(values)
        self.buffer = io.BytesIO()
        footer_left = " · ".join(p for p in (form.issuer, form.issuer_unit) if p) or form.title
        self.c = _NumberedCanvas(self.buffer, pagesize=A4, footer=(footer_left, self.words["page"]))
        title = self.variant.title if self.variant else form.title
        self.c.setTitle(_latin(f"{self._code()} {title}".strip()))
        self.c.setAuthor(_latin(form.issuer or "AI Circus"))
        self.y = PAGE_H - MARGIN
        self.section_number = 0

    # ── page furniture ────────────────────────────────────────────────────────
    def _code(self) -> str:
        return (self.variant.code if self.variant else self.form.code) or ""

    def _new_page(self, first: bool = False) -> None:
        if not first:
            self.c.showPage()
        if self.filing is None:
            self._watermark()
        self.y = PAGE_H - MARGIN
        self._header(compact=not first)

    def _watermark(self) -> None:
        c = self.c
        c.saveState()
        c.setFillColor(WATERMARK)
        c.setFont("Helvetica-Bold", 52)
        c.translate(PAGE_W / 2, PAGE_H / 2)
        c.rotate(38)
        c.drawCentredString(0, -20, _latin(self.words["draft"]))
        c.restoreState()

    def _emblem(self, x: float, y: float, r: float) -> None:
        c = self.c
        c.setStrokeColor(PRIMARY)
        c.setFillColor(PRIMARY)
        c.circle(x, y, r, stroke=0, fill=1)
        c.setStrokeColor(white)
        c.setLineWidth(0.8)
        c.circle(x, y, r - 3, stroke=1, fill=0)
        c.setLineWidth(0.4)
        c.circle(x, y, r - 5, stroke=1, fill=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", r * 0.62)
        c.drawCentredString(x, y - r * 0.22, _latin(_initials(self.form.issuer or self.form.title)))

    def _header(self, compact: bool) -> None:
        c = self.c
        top = self.y
        c.setFillColor(PRIMARY)
        c.rect(MARGIN, top - 3, CONTENT_W, 3, stroke=0, fill=1)
        if compact:
            c.setFont("Helvetica-Bold", 8)
            c.setFillColor(INK)
            c.drawString(MARGIN, top - 15, _latin(self.form.issuer or self.form.title))
            c.drawRightString(PAGE_W - MARGIN, top - 15, _latin(self._code()))
            self.y = top - 26
            return
        has_issuer = self.form.issuer is not None
        text_x = MARGIN
        if has_issuer:
            self._emblem(MARGIN + 21, top - 29, 20)
            text_x = MARGIN + 50
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 12)
            c.drawString(text_x, top - 25, _latin(self.form.issuer or ""))
            if self.form.issuer_unit:
                c.setFont("Helvetica", 7)
                c.setFillColor(MUTED)
                c.drawString(text_x, top - 36, _latin(self.form.issuer_unit))
        code = self._code()
        if code:
            label, _, number = code.partition(" ") if code.lower().startswith(("modelo ", "form ")) else ("", "", code)
            box_w, box_h = 132, 40
            bx, by = PAGE_W - MARGIN - box_w, top - 12 - box_h
            c.setStrokeColor(INK)
            c.setLineWidth(1.4)
            c.roundRect(bx, by, box_w, box_h, 3, stroke=1, fill=0)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 6.5)
            c.drawString(bx + 7, by + box_h - 11, _latin((label or self.words["model"]).upper()))
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 19 if len(number) <= 8 else 12)
            c.drawString(bx + 7, by + 9, _latin(number))
            c.setFont("Helvetica-Oblique", 6)
            c.setFillColor(MUTED)
            c.drawRightString(bx + box_w, top - 8, _latin(self.words["copy"]))
        title = self.variant.title if self.variant else self.form.title
        c.setFillColor(INK)
        lines = simpleSplit(_latin(title), "Helvetica-Bold", 13, CONTENT_W - 150)
        ty = top - (68 if has_issuer or code else 28)
        c.setFont("Helvetica-Bold", 13)
        for line in lines:
            c.drawString(MARGIN, ty, line)
            ty -= 15
        c.setStrokeColor(INK)
        c.setLineWidth(0.8)
        c.line(MARGIN, ty + 4, PAGE_W - MARGIN, ty + 4)
        self.y = ty - 8

    def _ensure(self, height: float) -> None:
        if self.y - height < FOOTER_H + 2:
            self._new_page()

    # ── sections and boxes ────────────────────────────────────────────────────
    def _section_band(self, section: FormSection) -> None:
        c = self.c
        numbered = section.id != "_all"
        self.section_number += 1
        band_h = 15
        c.setFillColor(TINT)
        c.rect(MARGIN, self.y - band_h, CONTENT_W, band_h, stroke=0, fill=1)
        x = MARGIN + 6
        if numbered:
            c.setFillColor(PRIMARY)
            c.rect(MARGIN, self.y - band_h, band_h, band_h, stroke=0, fill=1)
            c.setFillColor(white)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawCentredString(MARGIN + band_h / 2, self.y - 10.8, str(self.section_number))
            x = MARGIN + band_h + 6
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x, self.y - 10.6, _latin(section.title.upper()))
        self.y -= band_h
        if section.note:
            c.setFont("Helvetica-Oblique", 6.5)
            c.setFillColor(MUTED)
            c.drawString(MARGIN + 2, self.y - 8.5, _latin(section.note))
            self.y -= 11
        self.y -= 2

    def _box_height(self, spec: FormFieldSpec, width: float) -> float:
        value = format_value(spec, self.values.get(spec.id, ""), self.form.locale)
        label_lines = simpleSplit(_latin(spec.label), "Helvetica", 6.3, width - (26 if spec.casilla else 8))
        if spec.type == "checkbox":
            label_lines = simpleSplit(_latin(spec.label), "Helvetica", 7.5, width - (44 if spec.casilla else 24))
            return max(24.0, 10 + 9 * len(label_lines))
        head = 5 + 7.5 * len(label_lines)
        if spec.type == "textarea":
            lines = simpleSplit(_latin(value), "Helvetica", 9, width - 10) if value else []
            return head + 12 * max(3, len(lines)) + 6
        lines = simpleSplit(_latin(value), "Helvetica", 9.5, width - 10) if value else [""]
        return max(26.0, head + 12 * len(lines) + 4)

    def _box(self, spec: FormFieldSpec, x: float, top: float, width: float, height: float) -> None:
        c = self.c
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.rect(x, top - height, width, height, stroke=1, fill=0)
        if spec.casilla:
            c.setFillColor(PRIMARY)
            c.rect(x + width - 17, top - 9, 17, 9, stroke=0, fill=1)
            c.setFillColor(white)
            c.setFont("Courier-Bold", 6.8)
            c.drawCentredString(x + width - 8.5, top - 6.8, _latin(spec.casilla))
        raw = self.values.get(spec.id, "")
        if spec.type == "checkbox":
            size = 9
            bx, by = x + 6, top - height / 2 - size / 2
            c.setStrokeColor(INK)
            c.setLineWidth(0.8)
            c.rect(bx, by, size, size, stroke=1, fill=0)
            if is_checked(raw):
                c.setLineWidth(1.5)
                c.line(bx + 1.5, by + 1.5, bx + size - 1.5, by + size - 1.5)
                c.line(bx + 1.5, by + size - 1.5, bx + size - 1.5, by + 1.5)
            lines = simpleSplit(_latin(spec.label), "Helvetica", 7.5, width - (44 if spec.casilla else 24))
            c.setFillColor(INK)
            c.setFont("Helvetica", 7.5)
            ly = top - height / 2 + (len(lines) - 1) * 4.5 - 2.5
            for line in lines:
                c.drawString(bx + size + 6, ly, line)
                ly -= 9
            return
        label_lines = simpleSplit(_latin(spec.label), "Helvetica", 6.3, width - (26 if spec.casilla else 8))
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 6.3)
        ly = top - 7
        for line in label_lines:
            c.drawString(x + 4, ly, line)
            ly -= 7.5
        value = format_value(spec, raw, self.form.locale)
        c.setFillColor(INK)
        if spec.type == "textarea":
            c.setStrokeColor(TINT)
            c.setLineWidth(0.5)
            rule_y = ly - 10
            while rule_y > top - height + 4:
                c.line(x + 4, rule_y - 2.5, x + width - 4, rule_y - 2.5)
                rule_y -= 12
            c.setFont("Helvetica", 9)
            vy = ly - 10
            for line in simpleSplit(_latin(value), "Helvetica", 9, width - 10):
                c.drawString(x + 5, vy, line)
                vy -= 12
            return
        c.setFont("Helvetica", 9.5)
        lines = simpleSplit(_latin(value), "Helvetica", 9.5, width - 10) or [""]
        vy = ly - 8.5
        for line in lines:
            if spec.type in ("currency", "number"):
                c.drawRightString(x + width - 5, vy, line)
            else:
                c.drawString(x + 5, vy, line)
            vy -= 12

    def _section(self, section: FormSection, fields: list[FormFieldSpec]) -> None:
        rows = _rows(fields)
        first_row_h = max(self._box_height(s, span * COLUMN_W) for s, span in rows[0])
        self._ensure(40 + first_row_h)
        self._section_band(section)
        for row in rows:
            height = max(self._box_height(spec, span * COLUMN_W) for spec, span in row)
            self._ensure(height)
            x = MARGIN
            for spec, span in row:
                self._box(spec, x, self.y, span * COLUMN_W, height)
                x += span * COLUMN_W
            self.y -= height
        self.y -= 6

    # ── signature, stamp, receipt ─────────────────────────────────────────────
    def _signature_and_reserved(self) -> None:
        c = self.c
        self._ensure(66)
        half = CONTENT_W / 2 - 4
        top = self.y
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.rect(MARGIN, top - 62, half, 62, stroke=1, fill=0)
        c.setFont("Helvetica", 6.3)
        c.setFillColor(MUTED)
        c.drawString(MARGIN + 4, top - 8, _latin("Firma" if self.form.locale == "es" else "Signature"))
        who = applicant_name(self.form, self.values)
        c.setFillColor(INK)
        if self.filing is not None:
            when = self.filing.submitted_at.strftime("%d/%m/%Y %H:%M")
            text = self.words["signed"].format(who=who or "-", when=when)
            c.setFont("Helvetica-Oblique", 8)
            for i, line in enumerate(simpleSplit(_latin(text), "Helvetica-Oblique", 8, half - 12)):
                c.drawString(MARGIN + 6, top - 42 - i * 10, line)
            c.setStrokeColor(PRIMARY)
            c.setLineWidth(1.1)
            path = c.beginPath()
            sx, sy = MARGIN + 14, top - 22
            path.moveTo(sx, sy)
            path.curveTo(sx + 18, sy + 16, sx + 26, sy - 12, sx + 44, sy + 4)
            path.curveTo(sx + 58, sy + 16, sx + 64, sy - 6, sx + 92, sy + 2)
            c.drawPath(path, stroke=1, fill=0)
        else:
            c.setFont("Helvetica-Oblique", 8)
            c.setFillColor(MUTED)
            c.drawString(MARGIN + 6, top - 40, _latin(self.words["unsigned"]))
        rx = MARGIN + half + 8
        c.setStrokeColor(RULE)
        c.setDash(2, 2)
        c.rect(rx, top - 62, half, 62, stroke=1, fill=0)
        c.setDash()
        c.setFont("Helvetica", 6.3)
        c.setFillColor(MUTED)
        c.drawString(rx + 4, top - 8, _latin(self.words["reserved"]))
        if self.filing is not None:
            self._stamp(rx + half / 2, top - 36, scale=0.8)
        self.y = top - 68

    def _stamp(self, cx: float, cy: float, scale: float = 1.0) -> None:
        c = self.c
        assert self.filing is not None
        c.saveState()
        c.translate(cx, cy)
        c.scale(scale, scale)
        c.rotate(-5)
        c.setStrokeColor(STAMP)
        c.setFillColor(STAMP)
        c.setLineWidth(1.6)
        c.roundRect(-90, -25, 180, 50, 6, stroke=1, fill=0)
        c.setLineWidth(0.6)
        c.roundRect(-86, -21, 172, 42, 4, stroke=1, fill=0)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawCentredString(0, 10, _latin(self.words["registry"]))
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(0, 1.5, _latin(f"{self.words['entry']} · {self.filing.submitted_at:%d/%m/%Y %H:%M}"))
        c.setFont("Courier-Bold", 7.5)
        c.drawCentredString(0, -9.5, _latin(f"{self.words['no']} {self.filing.case_number}"))
        c.setFont("Helvetica", 5.5)
        c.drawCentredString(0, -17, _latin(self.form.issuer or self.form.title))
        c.restoreState()

    def _receipt(self) -> None:
        assert self.filing is not None
        filing = self.filing
        self._new_page()
        c = self.c
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(MARGIN, self.y - 18, _latin(self.words["receipt"]))
        self.y -= 34
        filled = sum(1 for spec in self.form.active_fields(self.values) if self.values.get(spec.id, "").strip())
        code = self._code()
        rows = [
            (self.words["reg_no"], filing.case_number),
            (self.words["submitted_at"], filing.submitted_at.strftime("%d/%m/%Y %H:%M:%S UTC")),
            (self.words["applicant"], applicant_name(self.form, self.values) or "-"),
            (self.words["model"], f"{code} · {self.variant.title if self.variant else self.form.title}".strip(" ·")),
            (self.words["issuer"], self.form.issuer or self.form.title),
            (self.words["csv"], filing.verification_code),
        ]
        label_w = 170
        for label, value in rows:
            lines = simpleSplit(_latin(value), "Helvetica-Bold", 10, CONTENT_W - label_w - 12)
            height = max(24.0, 10 + 12 * len(lines))
            c.setStrokeColor(RULE)
            c.setLineWidth(0.6)
            c.setFillColor(TINT)
            c.rect(MARGIN, self.y - height, label_w, height, stroke=1, fill=1)
            c.rect(MARGIN + label_w, self.y - height, CONTENT_W - label_w, height, stroke=1, fill=0)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 8)
            c.drawString(MARGIN + 6, self.y - 15, _latin(label))
            c.setFillColor(INK)
            c.setFont("Courier-Bold" if label in (self.words["reg_no"], self.words["csv"]) else "Helvetica-Bold", 10)
            for i, line in enumerate(lines):
                c.drawString(MARGIN + label_w + 8, self.y - 15 - i * 12, line)
            self.y -= height
        c.setFont("Helvetica", 7.5)
        c.setFillColor(MUTED)
        c.drawString(MARGIN, self.y - 14, _latin(self.words["boxes"].format(filled=filled)))
        self.y -= 34

        qr_size = 118
        widget = qr.QrCodeWidget(f"https://sede.example/verificar?csv={filing.verification_code}")
        x0, y0, x1, y1 = widget.getBounds()
        drawing = Drawing(qr_size, qr_size, transform=[qr_size / (x1 - x0), 0, 0, qr_size / (y1 - y0), 0, 0])
        drawing.add(widget)
        renderPDF.draw(drawing, c, MARGIN, self.y - qr_size)
        barcode = code128.Code128(filing.case_number, barHeight=38, barWidth=0.95)
        barcode.drawOn(c, MARGIN + qr_size + 30, self.y - 58)
        c.setFont("Courier", 8)
        c.setFillColor(INK)
        c.drawString(MARGIN + qr_size + 40, self.y - 70, _latin(filing.case_number))
        c.setFont("Helvetica", 7.5)
        c.setFillColor(MUTED)
        for i, line in enumerate(simpleSplit(_latin(self.words["verify"]), "Helvetica", 7.5, CONTENT_W - qr_size - 40)):
            c.drawString(MARGIN + qr_size + 30, self.y - 90 - i * 10, line)
        self._stamp(PAGE_W - MARGIN - 100, self.y - qr_size - 50)
        self.y -= qr_size + 110
        c.setFont("Helvetica-Oblique", 7.5)
        c.setFillColor(STAMP)
        c.drawString(MARGIN, self.y, _latin(self.words["demo"]))

    def render(self) -> bytes:
        self._new_page(first=True)
        for section, fields in _ordered_sections(self.form, self.values):
            self._section(section, fields)
        self._signature_and_reserved()
        if self.filing is not None:
            self._receipt()
        self.c.save()
        return self.buffer.getvalue()


def render_form_pdf(form: FormConfig, values: dict[str, str], filing: Filing | None = None) -> bytes:
    """The form filled with `values` as PDF bytes — a watermarked draft when `filing`
    is None, else the filed copy (registry stamp + signature + receipt page)."""
    return _Renderer(form, values, filing).render()
