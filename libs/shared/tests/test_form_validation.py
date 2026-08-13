"""Tests for ai_circus_shared.form_validation's generic field/submission validators."""

from __future__ import annotations

from ai_circus_shared.form_validation import validate_field, validate_submission
from ai_circus_shared.scenario_schema import FormConfig, FormFieldSpec, RequiredIf

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
