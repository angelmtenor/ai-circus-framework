#!/usr/bin/env python3
"""Generate the fictional sample documents of the `sede_electronica` scenario.

Five documents a user can drop into the chat with one click (see the scenario's
`form.sample_uploads`), all about the same fictional person, all marked as specimens:

- `dni_lucia_fernandez.png` — an identity card, front and back, in a deliberately
  generic design (not a copy of any real card's layout or security features);
  the old address (Madrid) is on the back;
- `certificado_empadronamiento.pdf` — a census certificate with the new address in
  the fictional municipality of Villaclara (Sevilla) → the change-of-address model;
- `notificacion_liquidacion.pdf` — a tax assessment of 3,842.17 € → the instalments model;
- `justificantes_pago_duplicado.pdf` — the same payment made twice → the refund model;
- `certificado_titularidad_bancaria.pdf` — the IBAN the refund / direct debit goes to.

Test values only: DNI 12345678Z (valid control letter), IBAN ES30 9999 0001 2301 2345
6789 (valid check digits, bank code 9999 belongs to no bank), "Banco Ejemplo, S.A.".

Run: `cd services/form-agent && uv run python ../../scripts/generate_sede_sample_documents.py`
(reportlab + Pillow). Deterministic — re-running rewrites identical content.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "scenarios/sede_electronica/sample_uploads"
FONTS = Path("/usr/share/fonts/truetype/dejavu")
W, H = A4
M = 56

PERSON = {
    "nombre": "LUCÍA",
    "apellidos": "FERNÁNDEZ ORTIZ",
    "nif": "12345678Z",
    "nacimiento": "12 04 1986",
    "lugar_nacimiento": "SEVILLA",
    "domicilio_antiguo": "C. MAYOR 14, PISO 3 B",
    "municipio_antiguo": "MADRID",
    "provincia_antigua": "MADRID",
    "iban": "ES30 9999 0001 2301 2345 6789",
}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = FONTS / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default(size)


# ── identity card (PNG) ──────────────────────────────────────────────────────


def _card(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    draw.rounded_rectangle((x, y, x + w, y + h), radius=36, fill="#eef3f1", outline="#8aa39b", width=3)
    for i in range(170, w - 170, 18):  # a faint guilloche-like band, clearly decorative
        draw.arc((x + i - 160, y + h - 170, x + i + 160, y + h + 150), 200, 340, fill="#d5e3de", width=1)
    draw.rounded_rectangle((x, y, x + w, y + 92), radius=36, fill="#1f5c4f")
    draw.rectangle((x, y + 56, x + w, y + 92), fill="#1f5c4f")


def _label_value(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, value: str, size: int = 38) -> None:
    draw.text((x, y), label, font=_font(20), fill="#4d6a62")
    draw.text((x, y + 26), value, font=_font(size, bold=True), fill="#10231e")


def identity_card() -> Image.Image:
    cw, ch, pad = 1180, 744, 60
    img = Image.new("RGB", (cw + 2 * pad, 2 * ch + 3 * pad), "#f7f7f4")
    draw = ImageDraw.Draw(img)

    # Front
    x, y = pad, pad
    _card(draw, x, y, cw, ch)
    draw.text((x + 40, y + 22), "DOCUMENTO DE IDENTIDAD", font=_font(40, bold=True), fill="#ffffff")
    draw.text((x + cw - 360, y + 30), "ESPÉCIMEN · FICTICIO", font=_font(28, bold=True), fill="#ffd166")
    draw.rounded_rectangle((x + 40, y + 130, x + 330, y + 500), radius=18, fill="#c9d8d3")
    draw.ellipse((x + 120, y + 180, x + 250, y + 310), fill="#8fa9a1")
    draw.rounded_rectangle((x + 80, y + 320, x + 290, y + 500), radius=90, fill="#8fa9a1")
    col = x + 380
    _label_value(draw, col, y + 125, "APELLIDOS", PERSON["apellidos"])
    _label_value(draw, col, y + 215, "NOMBRE", PERSON["nombre"])
    _label_value(draw, col, y + 305, "SEXO", "F", 34)
    _label_value(draw, col + 170, y + 305, "NACIONALIDAD", "ESP", 34)
    _label_value(draw, col + 450, y + 305, "FECHA DE NACIMIENTO", PERSON["nacimiento"], 34)
    _label_value(draw, col, y + 395, "EMISIÓN", "03 02 2021", 34)
    _label_value(draw, col + 300, y + 395, "VÁLIDO HASTA", "03 02 2031", 34)
    draw.text((x + 40, y + 540), "DNI / NIF", font=_font(24), fill="#4d6a62")
    draw.text((x + 40, y + 570), PERSON["nif"], font=_font(64, bold=True), fill="#10231e")
    draw.text((x + 520, y + 600), "Documento de demostración · sin validez", font=_font(26), fill="#6b837c")

    # Back
    x, y = pad, ch + 2 * pad
    _card(draw, x, y, cw, ch)
    draw.text((x + 40, y + 22), "DOCUMENTO DE IDENTIDAD · REVERSO", font=_font(40, bold=True), fill="#ffffff")
    draw.text((x + cw - 250, y + 30), "ESPÉCIMEN", font=_font(28, bold=True), fill="#ffd166")
    _label_value(draw, x + 40, y + 130, "DOMICILIO", PERSON["domicilio_antiguo"])
    _label_value(draw, x + 40, y + 220, "MUNICIPIO", PERSON["municipio_antiguo"])
    _label_value(draw, x + 560, y + 220, "PROVINCIA", PERSON["provincia_antigua"])
    _label_value(draw, x + 40, y + 310, "LUGAR DE NACIMIENTO", PERSON["lugar_nacimiento"])
    _label_value(draw, x + 560, y + 310, "HIJA DE", "ANTONIO / CARMEN", 34)
    mrz = [
        "IDESPDEMO00000<12345678Z<<<<<<",
        "8604124F3102037ESP<<<<<<<<<<<0",
        "FERNANDEZ<ORTIZ<<LUCIA<<<<<<<<",
    ]
    draw.rectangle((x + 30, y + 440, x + cw - 30, y + 700), fill="#ffffff")
    for i, line in enumerate(mrz):
        draw.text((x + 60, y + 460 + i * 78), line, font=ImageFont.truetype(str(FONTS / "DejaVuSansMono.ttf"), 50), fill="#10231e")
    return img


# ── PDF documents ────────────────────────────────────────────────────────────


def _letterhead(c: canvas.Canvas, org: str, unit: str, color: str) -> None:
    c.setFillColor(HexColor(color))
    c.rect(0, H - 8, W, 8, stroke=0, fill=1)
    c.circle(M + 18, H - 62, 18, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(M + 18, H - 66, "".join(w[0] for w in org.split() if w[0].isupper())[:3])
    c.setFillColor(HexColor("#10231e"))
    c.setFont("Helvetica-Bold", 13)
    c.drawString(M + 46, H - 58, org)
    c.setFont("Helvetica", 8.5)
    c.setFillColor(HexColor("#5a6b66"))
    c.drawString(M + 46, H - 71, unit)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(HexColor("#b3261e"))
    c.drawRightString(W - M, H - 58, "ESPÉCIMEN · DOCUMENTO FICTICIO")


def _title(c: canvas.Canvas, text: str, y: float) -> float:
    c.setFillColor(HexColor("#10231e"))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(M, y, text)
    c.setLineWidth(0.8)
    c.line(M, y - 8, W - M, y - 8)
    return y - 34


def _paragraph(c: canvas.Canvas, text: str, y: float, size: float = 10.5, bold: bool = False) -> float:
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, size)
    c.setFillColor(HexColor("#10231e"))
    for line in simpleSplit(text, font, size, W - 2 * M):
        c.drawString(M, y, line)
        y -= size * 1.45
    return y - 8


def _table(c: canvas.Canvas, rows: list[tuple[str, str]], y: float, label_w: float = 190) -> float:
    for label, value in rows:
        c.setStrokeColor(HexColor("#9aaaa5"))
        c.setLineWidth(0.5)
        c.setFillColor(HexColor("#edf2f0"))
        c.rect(M, y - 22, label_w, 22, stroke=1, fill=1)
        c.rect(M + label_w, y - 22, W - 2 * M - label_w, 22, stroke=1, fill=0)
        c.setFillColor(HexColor("#4d6a62"))
        c.setFont("Helvetica", 8.5)
        c.drawString(M + 6, y - 14.5, label)
        c.setFillColor(HexColor("#10231e"))
        c.setFont("Helvetica-Bold", 10)
        c.drawString(M + label_w + 8, y - 15, value)
        y -= 22
    return y - 16


def _signature(c: canvas.Canvas, y: float, who: str, csv: str) -> None:
    c.setFont("Helvetica-Oblique", 9)
    c.setFillColor(HexColor("#10231e"))
    c.drawString(M, y, who)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(HexColor("#5a6b66"))
    c.drawString(M, y - 14, f"Documento firmado electrónicamente · CSV {csv} · Documento de demostración, sin validez.")


def empadronamiento(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Certificado de empadronamiento")
    _letterhead(c, "Ayuntamiento de Villaclara", "Provincia de Sevilla · Padrón Municipal de Habitantes", "#7a2e3a")
    y = _title(c, "Certificado de empadronamiento", H - 120)
    y = _paragraph(
        c,
        "D. Manuel Ruiz Campos, Secretario General del Ayuntamiento de Villaclara (Sevilla), CERTIFICA que, según "
        "los datos que constan en el Padrón Municipal de Habitantes, la persona que se indica figura inscrita en "
        "este municipio con los datos siguientes:",
        y,
    )
    y = _table(
        c,
        [
            ("Apellidos y nombre", "FERNÁNDEZ ORTIZ, LUCÍA"),
            ("DNI / NIE", "12345678Z"),
            ("Fecha de nacimiento", "12/04/1986"),
            ("Domicilio", "AVENIDA DE LA CONSTITUCIÓN, 27, 2º A"),
            ("Código postal y municipio", "41940 VILLACLARA"),
            ("Provincia", "SEVILLA"),
            ("Fecha de alta en el domicilio", "01/09/2026"),
            ("Causa de la alta", "Cambio de residencia (procedencia: Madrid)"),
        ],
        y,
    )
    y = _paragraph(
        c,
        "Y para que conste y surta efectos ante la Agencia Tributaria de Villaclara, expido el presente certificado "
        "en Villaclara, a 3 de septiembre de 2026.",
        y,
    )
    _signature(c, y - 20, "El Secretario General, Manuel Ruiz Campos", "VCL-PAD-7Q2M-44KX")
    c.save()


def liquidacion(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Notificación de liquidación provisional")
    _letterhead(c, "Agencia Tributaria de Villaclara", "Gestión Tributaria · Administración de demostración", "#1d4e6f")
    y = _title(c, "Notificación de liquidación provisional", H - 120)
    y = _table(
        c,
        [
            ("Destinatario", "LUCÍA FERNÁNDEZ ORTIZ"),
            ("NIF", "12345678Z"),
            ("Domicilio", "C. MAYOR 14, PISO 3 B · 28013 MADRID"),
            ("Concepto", "IRPF ejercicio 2025"),
            ("Nº de liquidación", "A4128036451207"),
            ("Fecha de notificación", "22/09/2026"),
        ],
        y,
    )
    y = _paragraph(
        c,
        "Como resultado del procedimiento de comprobación limitada, se practica liquidación provisional por el "
        "Impuesto sobre la Renta de las Personas Físicas del ejercicio 2025, al no haberse declarado rendimientos "
        "del trabajo procedentes de un segundo pagador.",
        y,
    )
    y = _table(
        c,
        [
            ("Cuota resultante", "3.521,40 €"),
            ("Intereses de demora", "320,77 €"),
            ("IMPORTE A INGRESAR", "3.842,17 €"),
            ("Fin del periodo voluntario", "05/11/2026"),
        ],
        y,
    )
    y = _paragraph(
        c,
        "Plazo de ingreso: notificada entre los días 16 y último del mes, hasta el día 5 del segundo mes posterior. "
        "Podrá solicitar el aplazamiento o fraccionamiento de la deuda antes de que finalice el periodo voluntario "
        "(modelo AF-10). Contra esta liquidación cabe recurso de reposición en el plazo de un mes.",
        y,
        size=9.5,
    )
    _signature(c, y - 20, "La Jefa de la Dependencia de Gestión Tributaria", "ATV-LIQ-3F8N-2WQD")
    c.save()


def pagos_duplicados(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Justificantes de ingreso")
    _letterhead(c, "Banco Ejemplo, S.A.", "Justificantes de pago de tributos · Banca electrónica", "#2f6b3a")
    y = _title(c, "Justificantes de ingreso (2)", H - 120)
    for i, (fecha, canal) in enumerate(
        [("25/06/2026", "Domiciliación bancaria"), ("27/06/2026", "Pago en banca electrónica")], start=1
    ):
        y = _paragraph(c, f"Justificante {i} de 2", y, size=11, bold=True)
        y = _table(
            c,
            [
                ("Entidad receptora", "Agencia Tributaria de Villaclara"),
                ("Concepto", "IRPF 2025 · segundo plazo (40%)"),
                ("NIF del obligado", "12345678Z · FERNÁNDEZ ORTIZ, LUCÍA"),
                ("Nº de justificante (NRC)", "NRC2026ES00318842A7"),
                ("Fecha del ingreso", fecha),
                ("Canal", canal),
                ("Importe", "1.236,40 €"),
                ("Cuenta de cargo", PERSON["iban"]),
            ],
            y,
        )
    _paragraph(
        c,
        "Nota del cliente: el segundo plazo se cargó por domiciliación y, por error, se volvió a pagar dos días "
        "después desde la banca electrónica con el mismo número de justificante.",
        y,
        size=9.5,
    )
    c.save()


def titularidad(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Certificado de titularidad de cuenta")
    _letterhead(c, "Banco Ejemplo, S.A.", "Oficina 0001 · Villaclara (Sevilla)", "#2f6b3a")
    y = _title(c, "Certificado de titularidad de cuenta", H - 120)
    y = _paragraph(
        c,
        "Banco Ejemplo, S.A. CERTIFICA que la cuenta que se indica a continuación está abierta en esta entidad y "
        "que figura como única titular la persona siguiente:",
        y,
    )
    y = _table(
        c,
        [
            ("Titular", "LUCÍA FERNÁNDEZ ORTIZ"),
            ("NIF", "12345678Z"),
            ("IBAN", PERSON["iban"]),
            ("BIC", "BEJEESX1XXX"),
            ("Fecha de apertura", "14/03/2012"),
            ("Situación", "Activa"),
        ],
        y,
    )
    y = _paragraph(c, "Y para que así conste, se expide el presente certificado en Villaclara, a 15 de septiembre de 2026.", y)
    _signature(c, y - 20, "Banco Ejemplo, S.A. · Oficina 0001", "BEJ-CRT-9KD2-M3PL")
    c.save()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    identity_card().save(OUT / "dni_lucia_fernandez.png", optimize=True)
    empadronamiento(OUT / "certificado_empadronamiento.pdf")
    liquidacion(OUT / "notificacion_liquidacion.pdf")
    pagos_duplicados(OUT / "justificantes_pago_duplicado.pdf")
    titularidad(OUT / "certificado_titularidad_bancaria.pdf")
    for path in sorted(OUT.iterdir()):
        print(f"{path.name}: {path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
