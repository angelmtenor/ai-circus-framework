"""Tests for the filled-form PDF renderer (layout helpers + both output flavours)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ai_circus_shared.scenario_schema import FormConfig, FormFieldSpec, ScenarioDefinition

from form_agent.core.pdf import Filing, _latin, _ordered_sections, _rows, applicant_name, format_value, render_form_pdf

SCENARIOS = Path(__file__).parents[3] / "scenarios"
SEDE = ScenarioDefinition.load(SCENARIOS / "sede_electronica/scenario.yaml").form
assert SEDE is not None


def _spec(field_type: str, **kwargs: object) -> FormFieldSpec:
    return FormFieldSpec(id="x", label="X", type=field_type, **kwargs)  # type: ignore[arg-type]


def test_amounts_and_dates_are_printed_in_the_forms_locale() -> None:
    assert format_value(_spec("currency"), "3842.17", "es") == "3.842,17 €"
    assert format_value(_spec("currency"), "3842,5", "es") == "3.842,50 €"
    assert format_value(_spec("currency"), "3842.17", "en") == "3,842.17 €"
    assert format_value(_spec("date"), "2026-09-28", "es") == "28/09/2026"
    assert format_value(_spec("date"), "not a date", "es") == "not a date"
    assert format_value(_spec("text"), "  Lucía ", "es") == "Lucía"


def test_rows_pack_twelve_columns_and_stretch_the_last_box() -> None:
    fields = [_spec("text", span=5), _spec("text", span=5), _spec("date"), _spec("textarea")]
    rows = _rows(fields)
    assert [[span for _, span in row] for row in rows] == [[5, 7], [12], [12]]


def test_sections_follow_the_selected_model_and_hide_the_classification_field() -> None:
    sections = _ordered_sections(SEDE, {"tramite": "cambio_domicilio"})
    ids = [section.id for section, _ in sections]
    assert ids == ["solicitante", "representante", "notificacion", "dc_nuevo", "documentacion", "firma"]
    assert all(spec.id != "tramite" for _, fields in sections for spec in fields)


def test_a_plain_form_prints_as_one_section() -> None:
    form = FormConfig(title="Intake", fields=[_spec("text")])
    [(section, fields)] = _ordered_sections(form, {})
    assert section.title == "Intake" and len(fields) == 1


def test_applicant_name_joins_the_configured_fields() -> None:
    values = {"nombre": "Lucía", "primer_apellido": "Fernández", "segundo_apellido": "", "nif": "12345678Z"}
    assert applicant_name(SEDE, values) == "Lucía Fernández 12345678Z"


def test_characters_outside_windows_1252_are_replaced_not_dropped() -> None:
    assert _latin("Año 2026 · 3.842,17 €") == "Año 2026 · 3.842,17 €"
    assert _latin("deuda ≤ 50.000") == "deuda ? 50.000"


def test_draft_and_filed_copies_render_as_pdf() -> None:
    values = {"tramite": "situacion_familiar", "nif": "12345678Z", "sf_situacion_3": "true", "sf_desc1_anio": "2019"}
    draft = render_form_pdf(SEDE, values)
    filing = Filing("SEDE_ELECTRONICA-7F3A91C2", datetime(2026, 9, 28, 15, 42, tzinfo=UTC), "ABCD-EFGH-JKLM")
    filed = render_form_pdf(SEDE, values, filing)

    assert draft.startswith(b"%PDF") and filed.startswith(b"%PDF")
    assert filed.count(b"/Type /Page\n") > draft.count(b"/Type /Page\n")  # + the receipt page
