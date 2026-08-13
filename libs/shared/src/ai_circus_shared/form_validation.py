"""Generic, domain-agnostic field/submission validation for `assisted_form` scenarios.

Every rule here is one of a small, reusable set of primitives (email/phone/pattern/
min_length) — a scenario supplies the domain-specific detail (e.g. a national ID
number's format) as plain data (`pattern`) in its `scenario.yaml`, never as new code
here. `ui-react`'s `formValidation.ts` mirrors these same rules client-side for
instant feedback; this module is the final authority at submission time (see
`form-agent`'s `POST /submissions/{slug}`).
"""

from __future__ import annotations

import re

from ai_circus_shared.scenario_schema import FormConfig, FormFieldSpec

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9 ()-]{7,20}$")


def validate_field(spec: FormFieldSpec, value: str) -> str | None:
    """Validate one non-empty field value against its `validation` rule.

    Returns an error message, or `None` if valid. Required-ness is `validate_submission`'s
    job, not this function's — an empty value is always valid here.
    """
    if not value:
        return None
    if spec.validation == "email" and not _EMAIL_RE.match(value):
        return "Must be a valid email address."
    if spec.validation == "phone" and not _PHONE_RE.match(value):
        return "Must be a valid phone number."
    if spec.validation == "pattern":
        assert spec.pattern is not None  # enforced by FormFieldSpec's own validator
        if not re.match(spec.pattern, value):
            return spec.helper_text or "Does not match the required format."
    if spec.validation == "min_length":
        assert spec.min_length is not None  # enforced by FormFieldSpec's own validator
        if len(value) < spec.min_length:
            return f"Must be at least {spec.min_length} characters."
    return None


def _is_required(spec: FormFieldSpec, fields: dict[str, str]) -> bool:
    if spec.required:
        return True
    if spec.required_if is not None:
        return fields.get(spec.required_if.field) in spec.required_if.in_values
    return False


def validate_submission(form: FormConfig, fields: dict[str, str]) -> dict[str, str]:
    """Validate a full submission against `form`'s field specs.

    Returns `{field_id: error_message}` for every missing-required or invalid field —
    an empty dict means the submission is ready to persist.
    """
    errors: dict[str, str] = {}
    for spec in form.fields:
        value = fields.get(spec.id, "").strip()
        if not value:
            if _is_required(spec, fields):
                errors[spec.id] = "This field is required."
            continue
        error = validate_field(spec, value)
        if error is not None:
            errors[spec.id] = error
    return errors
