"""Generic field/submission validation for `assisted_form` scenarios.

Every rule here is one of a small, reusable set of primitives (email/phone/pattern/
min_length, numeric types, and two published checksums a field opts into: IBAN
mod-97 and the Spanish NIF/NIE control letter) — a scenario supplies the domain
detail (e.g. a national ID number's format) as plain data in its `scenario.yaml`,
never as new code here. `ui-react`'s `formValidation.ts` mirrors these same rules
client-side for instant feedback; this module is the final authority at submission
time (see `form-agent`'s `POST /submissions/{slug}`).

Messages follow the form's `locale` (English or Spanish) — the same wording the
official-layout sheet prints next to each box.
"""

from __future__ import annotations

import re

from ai_circus_shared.scenario_schema import FormConfig, FormFieldSpec

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9 ()-]{7,20}$")
_NUMBER_RE = re.compile(r"^-?\d+([.,]\d+)?$")
_CURRENCY_RE = re.compile(r"^-?\d+([.,]\d{1,2})?$")
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
_DNI_RE = re.compile(r"^\d{8}[A-Z]$")
_NIE_RE = re.compile(r"^[XYZ]\d{7}[A-Z]$")
_NIF_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "required": "This field is required.",
        "checkbox_required": "This box must be ticked.",
        "email": "Must be a valid email address.",
        "phone": "Must be a valid phone number.",
        "pattern": "Does not match the required format.",
        "min_length": "Must be at least {n} characters.",
        "number": "Must be a number.",
        "currency": "Must be an amount with at most 2 decimals.",
        "iban": "Not a valid IBAN (check digits don't match).",
        "es_nif": "Not a valid DNI/NIE (the control letter doesn't match).",
        "option": "Must be one of the listed options.",
    },
    "es": {
        "required": "Casilla obligatoria.",
        "checkbox_required": "Debe marcar esta casilla.",
        "email": "Correo electrónico no válido.",
        "phone": "Teléfono no válido.",
        "pattern": "El formato no es correcto.",
        "min_length": "Mínimo {n} caracteres.",
        "number": "Debe ser un número.",
        "currency": "Importe no válido (máximo 2 decimales).",
        "iban": "IBAN no válido (los dígitos de control no cuadran).",
        "es_nif": "DNI/NIE no válido (la letra de control no coincide).",
        "option": "Debe ser una de las opciones de la lista.",
    },
}


def normalize_compact(value: str) -> str:
    """Uppercase and drop spaces/dashes/dots — how an IBAN or NIF is compared."""
    return re.sub(r"[\s.-]", "", value).upper()


def iban_is_valid(value: str) -> bool:
    """ISO 13616: move the first four characters to the end, letters to 10-35, mod 97 == 1."""
    iban = normalize_compact(value)
    if not _IBAN_RE.match(iban):
        return False
    digits = "".join(str(int(ch, 36)) for ch in iban[4:] + iban[:4])
    return int(digits) % 97 == 1


def es_nif_is_valid(value: str) -> bool:
    """A Spanish DNI (8 digits + letter) or NIE (X/Y/Z + 7 digits + letter) whose
    control letter is `TRWAGMYFPDXBNJZSQVHLCKE[number mod 23]`."""
    nif = normalize_compact(value)
    if _NIE_RE.match(nif):
        nif = str("XYZ".index(nif[0])) + nif[1:]
    elif not _DNI_RE.match(nif):
        return False
    return _NIF_LETTERS[int(nif[:8]) % 23] == nif[8]


def is_checked(value: str) -> bool:
    """A checkbox value is ticked only when it's literally "true" (any case)."""
    return value.strip().lower() == "true"


def validate_field(spec: FormFieldSpec, value: str, locale: str = "en") -> str | None:
    """Validate one non-empty field value against its type and `validation` rule.

    Returns an error message, or `None` if valid. Required-ness is `validate_submission`'s
    job, not this function's — an empty value is always valid here.
    """
    if not value:
        return None
    msg = MESSAGES.get(locale, MESSAGES["en"])
    if spec.type == "number" and not _NUMBER_RE.match(value):
        return msg["number"]
    if spec.type == "currency" and not _CURRENCY_RE.match(value):
        return msg["currency"]
    if spec.type == "select" and spec.options and value not in spec.options:
        return msg["option"]
    if spec.validation == "email" and not _EMAIL_RE.match(value):
        return msg["email"]
    if spec.validation == "phone" and not _PHONE_RE.match(value):
        return msg["phone"]
    if spec.validation == "pattern":
        assert spec.pattern is not None  # enforced by FormFieldSpec's own validator
        if not re.match(spec.pattern, value):
            return spec.helper_text or msg["pattern"]
    if spec.validation == "min_length":
        assert spec.min_length is not None  # enforced by FormFieldSpec's own validator
        if len(value) < spec.min_length:
            return msg["min_length"].format(n=spec.min_length)
    if spec.validation == "iban" and not iban_is_valid(value):
        return msg["iban"]
    if spec.validation == "es_nif" and not es_nif_is_valid(value):
        return msg["es_nif"]
    return None


def _is_required(spec: FormFieldSpec, fields: dict[str, str]) -> bool:
    if spec.required:
        return True
    if spec.required_if is not None:
        return fields.get(spec.required_if.field) in spec.required_if.in_values
    return False


def validate_submission(form: FormConfig, fields: dict[str, str]) -> dict[str, str]:
    """Validate a full submission against `form`'s field specs — the shared fields
    plus, for a multi-model form, the selected variant's own.

    Returns `{field_id: error_message}` for every missing-required or invalid field —
    an empty dict means the submission is ready to persist.
    """
    msg = MESSAGES.get(form.locale, MESSAGES["en"])
    errors: dict[str, str] = {}
    for spec in form.active_fields(fields):
        value = fields.get(spec.id, "").strip()
        if spec.type == "checkbox":
            if _is_required(spec, fields) and not is_checked(value):
                errors[spec.id] = msg["checkbox_required"]
            continue
        if not value:
            if _is_required(spec, fields):
                errors[spec.id] = msg["required"]
            continue
        error = validate_field(spec, value, form.locale)
        if error is not None:
            errors[spec.id] = error
    return errors
