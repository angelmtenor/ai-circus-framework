"""Tests for ai_circus_shared.form_validation's generic field/submission validators."""

from __future__ import annotations

from ai_circus_shared.form_validation import (
    es_nif_is_valid,
    iban_is_valid,
    is_checked,
    validate_field,
    validate_submission,
)
from ai_circus_shared.scenario_schema import FormConfig, FormFieldSpec, FormSection, FormVariant, RequiredIf

NAME = FormFieldSpec(id="full_name", label="Full name", type="text", required=True)
EMAIL = FormFieldSpec(id="email", label="Email", type="email", required=True, validation="email")
PHONE = FormFieldSpec(id="phone", label="Phone", type="tel", required=True, validation="phone")
ID_NUMBER = FormFieldSpec(
    id="id_number",
    label="ID number",
    type="text",
    required=True,
    validation="pattern",
    pattern=r"^[A-Z0-9]{6,12}$",
    helper_text="Must be 6-12 uppercase letters/digits.",
)
DESCRIPTION = FormFieldSpec(
    id="description", label="Description", type="textarea", required=True, validation="min_length", min_length=20
)
REQUEST_TYPE = FormFieldSpec(id="request_type", label="Request type", type="select", options=["a", "b"], required=True)
ADDRESS = FormFieldSpec(
    id="address",
    label="Address",
    type="text",
    required_if=RequiredIf(field="request_type", in_values=["a"]),
)


def test_validate_field_email_accepts_valid_and_rejects_invalid() -> None:
    assert validate_field(EMAIL, "jane@example.com") is None
    assert validate_field(EMAIL, "not-an-email") is not None


def test_validate_field_phone_accepts_valid_and_rejects_invalid() -> None:
    assert validate_field(PHONE, "+1 (555) 123-4567") is None
    assert validate_field(PHONE, "abc") is not None


def test_validate_field_pattern_uses_scenario_supplied_regex() -> None:
    """The ID format is pure data (`pattern`) — no country-specific code involved."""
    assert validate_field(ID_NUMBER, "AB12345") is None
    assert validate_field(ID_NUMBER, "not-valid!") is not None


def test_validate_field_min_length() -> None:
    assert validate_field(DESCRIPTION, "short") is not None
    assert validate_field(DESCRIPTION, "this description is definitely long enough") is None


def test_validate_field_empty_value_is_always_valid() -> None:
    """Required-ness is validate_submission's job, not validate_field's."""
    assert validate_field(EMAIL, "") is None


def test_validate_submission_reports_missing_required_fields() -> None:
    form = FormConfig(title="t", fields=[NAME, EMAIL])

    errors = validate_submission(form, {"full_name": "Jane Doe"})

    assert errors == {"email": "This field is required."}


def test_validate_submission_reports_invalid_fields() -> None:
    form = FormConfig(title="t", fields=[EMAIL])

    errors = validate_submission(form, {"email": "not-an-email"})

    assert "email" in errors


def test_validate_submission_passes_when_everything_is_valid() -> None:
    form = FormConfig(title="t", fields=[NAME, EMAIL, DESCRIPTION])

    errors = validate_submission(
        form,
        {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "description": "this description is definitely long enough",
        },
    )

    assert errors == {}


def test_validate_submission_required_if_triggers_on_matching_value() -> None:
    """`address` is only required when request_type is one of its `in_values` — a
    generic conditional rule, not hardcoded to a classification concept.
    """
    form = FormConfig(title="t", fields=[REQUEST_TYPE, ADDRESS])

    errors = validate_submission(form, {"request_type": "a"})

    assert errors == {"address": "This field is required."}


def test_validate_submission_required_if_does_not_trigger_on_other_values() -> None:
    form = FormConfig(title="t", fields=[REQUEST_TYPE, ADDRESS])

    errors = validate_submission(form, {"request_type": "b"})

    assert errors == {}


# --- official-layout forms: checksums, numeric types, checkboxes, variants, locale ---

NIF = FormFieldSpec(id="nif", label="NIF", type="text", required=True, validation="es_nif")
IBAN = FormFieldSpec(id="iban", label="IBAN", type="text", validation="iban")
AMOUNT = FormFieldSpec(id="amount", label="Amount", type="currency")
COUNT = FormFieldSpec(id="count", label="Count", type="number")
DECLARE = FormFieldSpec(id="declare", label="I declare", type="checkbox", required=True)


def test_es_nif_checks_the_dni_and_nie_control_letter() -> None:
    assert es_nif_is_valid("12345678Z")
    assert es_nif_is_valid("12345678-z")  # separators and case are normalized
    assert not es_nif_is_valid("12345678A")
    assert es_nif_is_valid("X1234567L")  # NIE: X -> 0
    assert not es_nif_is_valid("X1234567A")
    assert not es_nif_is_valid("1234567Z")
    assert validate_field(NIF, "12345678A") is not None


def test_iban_checks_the_iso_13616_mod_97_digits() -> None:
    assert iban_is_valid("ES30 9999 0001 2301 2345 6789")
    assert iban_is_valid("GB82WEST12345698765432")
    assert not iban_is_valid("ES3199990001230123456789")
    assert not iban_is_valid("ES30")
    assert validate_field(IBAN, "ES3199990001230123456789") is not None


def test_numeric_types_accept_plain_decimals_only() -> None:
    assert validate_field(AMOUNT, "3842.17") is None
    assert validate_field(AMOUNT, "3842,17") is None
    assert validate_field(AMOUNT, "3.842,17 €") is not None
    assert validate_field(AMOUNT, "12.345") is not None  # 3 decimals is not an amount
    assert validate_field(COUNT, "12") is None
    assert validate_field(COUNT, "twelve") is not None


def test_select_values_must_be_listed_options() -> None:
    assert validate_field(REQUEST_TYPE, "a") is None
    assert validate_field(REQUEST_TYPE, "zzz") is not None


def test_a_required_checkbox_must_be_literally_true() -> None:
    form = FormConfig(title="t", fields=[DECLARE])
    assert validate_submission(form, {"declare": "false"}) == {"declare": "This box must be ticked."}
    assert validate_submission(form, {}) == {"declare": "This box must be ticked."}
    assert validate_submission(form, {"declare": "TRUE"}) == {}
    assert is_checked("true") and not is_checked("yes")


def _variant_form() -> FormConfig:
    return FormConfig(
        title="t",
        locale="es",
        classification_field="kind",
        classification_options=["general", "refund"],
        general_variant="general",
        sections=[FormSection(id="who", title="Who")],
        fields=[
            FormFieldSpec(id="kind", label="Kind", type="select", options=["general", "refund"], required=True),
            FormFieldSpec(id="name", label="Name", type="text", required=True, casilla="01", section="who"),
        ],
        variants=[
            FormVariant(key="general", code="G-1", title="General"),
            FormVariant(
                key="refund",
                code="R-1",
                title="Refund",
                sections=[FormSection(id="money", title="Money")],
                fields=[
                    FormFieldSpec(
                        id="amount", label="Amount", type="currency", required=True, casilla="20", section="money"
                    )
                ],
            ),
        ],
    )


def test_only_the_selected_variants_fields_are_validated_in_the_forms_locale() -> None:
    form = _variant_form()
    assert validate_submission(form, {"kind": "general", "name": "Lucía"}) == {}
    assert validate_submission(form, {"kind": "refund", "name": "Lucía"}) == {"amount": "Casilla obligatoria."}
    assert validate_submission(form, {"kind": "refund", "name": "Lucía", "amount": "x"}) == {
        "amount": "Importe no válido (máximo 2 decimales)."
    }
