#!/usr/bin/env python3
"""Generate the fictional scanned applications of the `prestaciones_sociales` case desk.

Four paper-looking *Solicitud de Ayuda Económica de Emergencia Social (Modelo AES-01)*
forms of the fictional Ayuntamiento de Villaclara — one per scenario of the demo — that
the desk reads with OCR + LLM extraction (see the scenario's `ui_extras.sample_uploads`):

- `solicitud_suministros_monoparental.pdf` — a complete application, a clear grant;
- `solicitud_alquiler_lanzamiento.pdf` — an eviction with a court date (priority review);
- `solicitud_otros_gastos_coche.pdf` — a non-basic expense, a clear denial;
- `solicitud_sin_dni.pdf` — the first one without the ID document: the rules stop it.

Each form is drawn from the scenario's own `personas` (same values the desk's "persona"
buttons use) and the labels of its `feature_schema`, so the scan and the scenario cannot
drift apart. It is then "scanned": slight rotation, grey paper, sensor noise, blur, a
registry stamp and a scribbled signature, and saved as an *image-only* PDF — the text has
to go through tesseract like a real scan. Everything is FICTIONAL and marked as a specimen.

Run: `cd services/form-agent && uv run python ../../scripts/generate_prestaciones_sample_documents.py`
(Pillow + the scenario schema). Deterministic — re-running rewrites identical pixels.
"""

from __future__ import annotations

import random
from pathlib import Path

from ai_circus_shared.scenario_schema import ScenarioDefinition
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "scenarios" / "prestaciones_sociales"
OUT = SCENARIO_DIR / "sample_uploads"
FONTS = Path("/usr/share/fonts/truetype/dejavu")
DPI = 200
W, H = 1654, 2339  # A4 at 200 dpi
MARGIN = 120
INK = 30
BODY = 31
SMALL = 25

# file -> (persona label, applicant name printed in the header, stamp date)
SCANS = {
    "solicitud_suministros_monoparental.pdf": ("Madre sola, aviso de corte", "Marta Ruiz Delgado", "14/03/2025"),
    "solicitud_alquiler_lanzamiento.pdf": ("Desahucio con lanzamiento", "Antonio Vargas Cortés", "02/04/2025"),
    "solicitud_otros_gastos_coche.pdf": ("Entrada de un coche", "Julio Serrano Blanco", "21/05/2025"),
    "solicitud_sin_dni.pdf": ("Sin DNI aportado", "Marta Ruiz Delgado", "09/06/2025"),
}


def font(size: int, bold: bool = False, mono: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSansMono.ttf" if mono else "DejaVuSans.ttf"
    if bold:
        name = "DejaVuSans-Bold.ttf"
    if italic:
        name = "DejaVuSerif-Italic.ttf" if (FONTS / "DejaVuSerif-Italic.ttf").exists() else "DejaVuSans-Oblique.ttf"
    return ImageFont.truetype(str(FONTS / name), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=fnt) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    return [*lines, line] if line else lines


class Sheet:
    """A growing multi-page form: a cursor, and helpers that start a new page when needed."""

    def __init__(self, header: list[str], authority: str, unit: str, code: str) -> None:
        self.pages: list[Image.Image] = []
        self.header = header
        self.authority, self.unit, self.code = authority, unit, code
        self.draw: ImageDraw.ImageDraw
        self.y = 0
        self._new_page()

    def _new_page(self) -> None:
        page = Image.new("L", (W, H), 255)
        self.pages.append(page)
        self.draw = ImageDraw.Draw(page)
        d = self.draw
        d.rectangle((MARGIN - 20, 70, W - MARGIN + 20, 210), outline=INK, width=3)
        d.text((MARGIN, 88), self.authority.upper(), font=font(36, bold=True), fill=INK)
        d.text((MARGIN, 140), self.unit, font=font(SMALL), fill=INK)
        d.text((W - MARGIN - 250, 98), self.code, font=font(34, bold=True), fill=INK)
        d.text((W - MARGIN - 330, 148), "Ejemplar para la administración", font=font(22), fill=INK)
        self.y = 250
        d.text((MARGIN, H - 90), "Documento ficticio de demostración · sin validez oficial", font=font(22), fill=90)
        d.text((W - MARGIN - 160, H - 90), f"Pág. {len(self.pages)}", font=font(22), fill=90)

    def need(self, height: int) -> None:
        if self.y + height > H - 140:
            self._new_page()

    def title(self, text: str) -> None:
        self.need(120)
        self.y += 8
        self.draw.rectangle((MARGIN, self.y, W - MARGIN, self.y + 52), fill=60)
        self.draw.text((MARGIN + 14, self.y + 8), text, font=font(30, bold=True), fill=255)
        self.y += 66

    def box(self, number: int, label: str, value: str, tall: bool = False) -> None:
        """A numbered casilla: small printed label, the filled-in value in another typeface."""
        label_font, value_font = font(SMALL), font(BODY, mono=not tall)
        value_lines = wrap(self.draw, value, value_font, W - 2 * MARGIN - 40) if tall else [value]
        height = 40 + 40 * max(1, len(value_lines)) + 14
        self.need(height)
        d = self.draw
        d.rectangle((MARGIN, self.y, W - MARGIN, self.y + height - 8), outline=INK, width=2)
        d.rectangle((MARGIN, self.y, MARGIN + 62, self.y + 36), fill=INK)
        d.text((MARGIN + 10, self.y + 4), f"{number:02d}", font=font(24, bold=True), fill=255)
        d.text((MARGIN + 76, self.y + 4), label, font=label_font, fill=INK)
        for i, line in enumerate(value_lines):
            d.text((MARGIN + 20, self.y + 44 + 40 * i), line, font=value_font, fill=15)
        self.y += height

    def signature_block(self, who: str) -> None:
        self.need(260)
        self.y += 30
        d = self.draw
        d.text((MARGIN, self.y), "Firma de la persona solicitante (declaración responsable):", font=font(SMALL), fill=INK)
        d.line((MARGIN, self.y + 190, MARGIN + 560, self.y + 190), fill=INK, width=2)
        d.text((MARGIN, self.y + 200), who, font=font(SMALL), fill=INK)
        rng = random.Random(len(who))
        points = []
        x = MARGIN + 30
        for i in range(34):
            x += rng.randint(8, 18)
            points.append((x, self.y + 120 + rng.randint(-45, 35) * (1 if i % 2 else 0.5)))
        d.line(points, fill=20, width=4, joint="curve")
        self.y += 260


def amount(value: float) -> str:
    return f"{value:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")


def render_form(definition: ScenarioDefinition, record: dict[str, float | str], name: str) -> list[Image.Image]:
    extras = definition.ui_extras
    dataset = definition.dataset
    assert extras is not None and dataset is not None and extras.kind == "case_desk"
    schema = {**dataset.feature_schema, **dataset.rule_columns}
    sheet = Sheet([], extras.authority, extras.authority_unit or "", extras.form_code or "")
    d = sheet.draw
    d.text((MARGIN, sheet.y), extras.form_title.upper(), font=font(34, bold=True), fill=INK)
    sheet.y += 56
    d.text((MARGIN, sheet.y), f"Persona solicitante: {name}", font=font(BODY), fill=INK)
    sheet.y += 54
    number = 0
    for section in extras.sections:
        sheet.title(section.title)
        for column in section.fields:
            spec = schema[column]
            number += 1
            value = record.get(column)
            if value is None:  # a column the persona leaves blank (the scan simply shows an empty box)
                shown = ""
            elif spec.type == "numeric":
                shown = (
                    amount(float(value))
                    if any(w in spec.label for w in ("(€)", "€"))
                    else (f"{float(value):g}".replace(".", ","))
                )
            else:
                shown = str(value)
            if spec.type == "categorical" and column in dataset.rule_columns:
                shown = shown.upper()  # "SÍ" / "NO" — a ticked document box, printed
            sheet.box(number, spec.label, shown, tall=spec.type == "text")
    sheet.signature_block(name)
    return sheet.pages


def scan(page: Image.Image, seed: int, stamp_date: str, first: bool) -> Image.Image:
    """Make a clean page look scanned: rotation, grey paper, noise, blur, a registry stamp."""
    rng = random.Random(seed)
    page = page.convert("L")
    if first:
        stamp = Image.new("L", (470, 190), 255)
        sd = ImageDraw.Draw(stamp)
        sd.rounded_rectangle((4, 4, 466, 186), radius=14, outline=95, width=5)
        sd.text((26, 22), "REGISTRO DE ENTRADA", font=font(30, bold=True), fill=95)
        sd.text((26, 74), "Ayto. de Villaclara", font=font(28), fill=95)
        sd.text((26, 122), f"Fecha: {stamp_date}", font=font(28), fill=95)
        inked = stamp.rotate(6, expand=True, fillcolor=255, resample=Image.Resampling.BICUBIC)
        page.paste(inked, (W - MARGIN - 470, 1140), ImageOps.invert(inked))  # ink only, the paper shows through
    page = page.rotate(rng.uniform(-0.9, 0.9), fillcolor=255, resample=Image.Resampling.BICUBIC)
    page = page.point(lambda v: int(v * 0.93 + 14))  # off-white paper, not pure white
    page = Image.blend(page, Image.effect_noise(page.size, 14), 0.07)  # sensor noise (gaussian around mid-grey)
    return page.filter(ImageFilter.GaussianBlur(0.7))


def main() -> None:
    definition = ScenarioDefinition.load(SCENARIO_DIR / "scenario.yaml")
    extras = definition.ui_extras
    assert extras is not None and extras.kind == "case_desk"
    personas = {p.label: p for p in extras.personas}
    OUT.mkdir(parents=True, exist_ok=True)
    for index, (filename, (label, name, date)) in enumerate(SCANS.items()):
        persona = personas[label]
        pages = [scan(p, 1000 * index + i, date, first=i == 0) for i, p in enumerate(render_form(definition, persona.record, name))]
        target = OUT / filename
        pages[0].save(target, "PDF", save_all=True, append_images=pages[1:], resolution=DPI)
        print(f"{target.relative_to(ROOT)}: {len(pages)} pages, {target.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
