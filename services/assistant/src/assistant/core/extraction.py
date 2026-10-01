"""
- Title:    LLM extraction of an application's boxes from OCR text (case_desk intake)
- Author:   Angel Martinez-Tenor

A scanned application is read in two steps: ui-react sends the file to platform-registry's
OCR (`/documents/extract`, tesseract spa+eng), then the OCR text comes here. The LLM maps
it onto the scenario's boxes (`feature_schema` + `rule_columns`) and must *quote* the
text each value comes from. Nothing it says is trusted blindly: categorical values must
be one of the options, numbers must parse and sit inside the schema's range, free text is
cut at its `max_length`, and a quote the OCR text does not contain downgrades the value
to "unverified" (low confidence, flagged in the UI) — the same pattern as the rubric
check. The deterministic rules and the model only ever see what a person can confirm.
"""

from __future__ import annotations

import re
from typing import Any

from ai_circus_shared.business_rules import fold
from ai_circus_shared.scenario_schema import TabularDataset

from assistant.core.rubric import extract_json

MAX_OCR_CHARS = 20000
MAX_QUOTE_CHARS = 240
UNVERIFIED_CONFIDENCE_CAP = 0.5


def extractable_fields(dataset: TabularDataset, skip: set[str]) -> dict[str, Any]:
    """The boxes the LLM may fill: features and rule columns, minus computed ones."""
    schema = {**dataset.feature_schema, **dataset.rule_columns}
    return {column: spec for column, spec in schema.items() if column not in skip}


def _describe(column: str, spec: Any) -> str:
    base = f'- "{column}" ({spec.label}): '
    if spec.type == "categorical":
        return base + "one of " + " | ".join(f'"{o}"' for o in spec.options)
    if spec.type == "numeric":
        return base + f"a number between {spec.min:g} and {spec.max:g} (no units, no thousands separators)"
    return base + f"free text, copied as written, at most {spec.max_length} characters"


def build_extraction_prompt(dataset: TabularDataset, skip: set[str], form_title: str) -> str:
    """System prompt: the boxes to fill, how to quote, what to leave out."""
    boxes = "\n".join(_describe(c, s) for c, s in extractable_fields(dataset, skip).items())
    return (
        f'You read the OCR text of a scanned paper form ("{form_title}") and copy its contents into '
        "structured boxes. The OCR text can contain misread characters, broken lines and the form's own "
        "printed labels: ignore labels, copy only what the applicant or the caseworker filled in.\n\n"
        f"Boxes:\n{boxes}\n\n"
        "Rules:\n"
        "- Fill a box ONLY if the text states it. Never guess, infer or invent: leave a box out when it is "
        "not there or unreadable.\n"
        '- For every box you fill, give an "evidence" quote: the exact words from the OCR text the value '
        "comes from (copy them character by character, at most 240 characters).\n"
        '- "confidence" is your certainty from 0 to 1 (lower it for misread or ambiguous text).\n'
        '- Documents boxes: "Sí" only when the form says the document is attached/provided, "No" only '
        "when it says it is not; if the form does not say, leave the box out.\n"
        "- Never write names, ID numbers, addresses or phone numbers into a free-text box.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fences:\n"
        '{"fields": {"<box id>": {"value": <value>, "evidence": "<exact quote>", "confidence": <0..1>}}}'
    )


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", fold(text)).strip()


def _number(value: object) -> float | None:
    """A number from '1.250,50 €', '1250.5', 1250 — Spanish and English formats."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d,.\-]", "", str(value))
    if not text or text in {"-", ".", ","}:
        return None
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        text = f"{head}.{tail}" if len(tail) != 3 or not head else text.replace(",", "")
    elif text.count(".") > 1 or (re.fullmatch(r"-?\d{1,3}\.\d{3}", text) is not None):
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def _coerce(value: object, spec: Any) -> tuple[str | float | None, str | None]:
    """(value fitted to the box, problem) — value None when it can't be used."""
    if spec.type == "categorical":
        wanted = _normalise(str(value))
        for option in spec.options:
            if _normalise(option) == wanted:
                return option, None
        return None, f"{str(value)[:40]!r} is not one of the options"
    if spec.type == "numeric":
        number = _number(value)
        if number is None:
            return None, f"{str(value)[:40]!r} is not a number"
        if not spec.min <= number <= spec.max:
            return None, f"{number:g} is outside {spec.min:g}-{spec.max:g}"
        return (int(number) if number == int(number) else round(number, 4)), None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return (text[: spec.max_length] or None), None if text else "empty"


def parse_extraction(content: str, dataset: TabularDataset, skip: set[str], ocr_text: str) -> dict[str, Any]:
    """Validate the model's JSON against the boxes; raise ValueError when it isn't JSON.

    Returns `{"fields": {box: {value, evidence, confidence, verified}}, "rejected": {box: why},
    "missing": [boxes not found]}` — every value already coerced to its box's type.
    """
    raw = extract_json(content)
    items = raw.get("fields")
    if not isinstance(items, dict):
        raise ValueError("The model's JSON has no 'fields' object.")
    schema = extractable_fields(dataset, skip)
    source = _normalise(ocr_text)
    fields: dict[str, Any] = {}
    rejected: dict[str, str] = {}
    for column, item in items.items():
        if column not in schema:
            continue
        if not isinstance(item, dict) or item.get("value") in (None, ""):
            continue
        value, problem = _coerce(item["value"], schema[column])
        if value is None:
            rejected[column] = problem or "unusable value"
            continue
        evidence = str(item.get("evidence") or "")[:MAX_QUOTE_CHARS].strip().strip("\"'“”")
        verified = bool(evidence) and _normalise(evidence) in source
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        if not verified:
            confidence = min(confidence, UNVERIFIED_CONFIDENCE_CAP)
        fields[column] = {
            "value": value,
            "evidence": evidence,
            "confidence": round(confidence, 2),
            "verified": verified,
        }
    missing = [c for c in schema if c not in fields and c not in rejected]
    return {"fields": fields, "rejected": rejected, "missing": missing}
