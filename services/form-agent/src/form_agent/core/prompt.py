"""
- Title:    Form-filling grounding (system prompt construction)
- Author:   Angel Martinez-Tenor

Entirely data-driven from `definition.form`/`definition.chat.context` — no
scenario-specific wording is baked in here, so the same function grounds any
`assisted_form` scenario: a plain intake form, a classification-driven one, or a
family of official models (general + specific, `form.variants`) with numbered boxes.
"""

from __future__ import annotations

from ai_circus_shared.scenario_schema import FormConfig, FormFieldSpec, ScenarioDefinition

# Stated once, not per field: a family of official models can have ~70 fields, and
# every token of this prompt is resent on each tool round trip (a free-tier model's
# tokens-per-minute cap is what that hits first).
_LEGEND = (
    'Field lines read: id [box number] "label": type, then * if always required. Value formats — '
    "date: YYYY-MM-DD; currency/number: plain number, dot decimal, no symbol or thousands separator "
    "(e.g. 3842.17); checkbox: 'true' to tick it, '' to untick; select: one of its options, verbatim."
)
_CHECKSUMS = {
    "es_nif": "Spanish DNI/NIE, control letter is checked",
    "iban": "IBAN, check digits are verified",
}


def _describe_field(spec: FormFieldSpec, seen_options: dict[tuple[str, ...], str]) -> str:
    """One compact line per field — id, box, label, type, and what makes it required,
    so the model knows exactly when it can stop asking about it. An option list
    already printed for an earlier field is referenced by that field's id.
    """
    line = f"- {spec.id}"
    if spec.casilla:
        line += f" [box {spec.casilla}]"
    line += f' "{spec.label}": {spec.type}'
    if spec.required:
        line += "*"
    elif spec.required_if is not None:
        line += f", required only if {spec.required_if.field!r} is one of {spec.required_if.in_values}"
    if spec.validation in _CHECKSUMS:
        line += f", {_CHECKSUMS[spec.validation]}"
    if spec.options:
        key = tuple(spec.options)
        if key in seen_options:
            line += f", options as {seen_options[key]!r}"
        else:
            seen_options[key] = spec.id
            line += ", options: " + " | ".join(spec.options)
    if spec.helper_text:
        line += f" ({spec.helper_text})"
    return line


def _describe_variants(form: FormConfig, seen_options: dict[tuple[str, ...], str]) -> str:
    """The model family: which classification value selects which printed model, and
    the boxes each specific model adds on top of the shared ones.
    """
    blocks = []
    for variant in form.variants:
        general = (
            " (the GENERAL model — use it when no specific model fits)" if variant.key == form.general_variant else ""
        )
        header = f"* {form.classification_field}={variant.key!r} -> {variant.code} '{variant.title}'{general}"
        if variant.summary:
            header += f": {variant.summary}"
        fields = "\n".join("  " + _describe_field(f, seen_options) for f in variant.fields)
        blocks.append(f"{header}\n{fields}" if fields else header)
    return (
        f"\n\nThis is a family of official models that share the fields above. The field "
        f"{form.classification_field!r} selects which model is filed; each model adds its own fields, "
        "which only exist (and are only required) while that model is selected:\n" + "\n".join(blocks)
    )


def build_form_system_prompt(definition: ScenarioDefinition) -> str:
    """Ground the assistant in the scenario's chat.context and its form's field catalog."""
    form = definition.form
    assert form is not None  # guaranteed by kind="assisted_form" filter
    seen_options: dict[tuple[str, ...], str] = {}
    fields_description = "\n".join(_describe_field(f, seen_options) for f in form.fields)

    classification_phrase = ""
    if form.classification_field is not None:
        classification_phrase = (
            f"\n\nThe field {form.classification_field!r} categorizes the request. Call the retrieve_catalog "
            f"tool to figure out which of these types applies, based on what the user describes, before "
            f"setting it: {form.classification_options}. If you're not confident yet, ask a clarifying "
            "question instead of guessing."
        )
        if form.general_variant is not None:
            classification_phrase += (
                f" If the request fits none of the specific models, set it to {form.general_variant!r} "
                "(the general model) and put the user's facts and request in its free-text boxes."
            )
    variants_phrase = _describe_variants(form, seen_options) if form.variants else ""

    return (
        f"You are a form-filling assistant for '{form.title}'.\n"
        f"{definition.chat.context.strip()}\n\n"
        f"The form has these fields. {_LEGEND}\n{fields_description}"
        f"{variants_phrase}"
        f"{classification_phrase}\n\n"
        "Whenever you learn or confirm a field's value from the conversation, call the update_form_fields tool "
        "right away with every field you now know — don't wait until the end of the conversation, and don't "
        "wait to be asked. Calling update_form_fields ends your turn, so write your short message to the user "
        "first (what you filled and what is still missing, or your question), then make ONE update_form_fields "
        "call with every value — including the classification field as soon as you know it. Never invent a "
        "value the user didn't actually state (a name, ID number, phone, or email you're not sure about is worse "
        "than leaving it blank). Use each field's stated format exactly.\n\n"
        "You'll also be told the form's current values and which required fields are still missing or invalid "
        "(as context, not something the user typed) — use that to avoid re-asking about fields that are already "
        "filled in correctly, and to explain concretely what's still needed when the user asks why they can't "
        "submit yet. When you mention a field to the user, prefer its label (and its box number, if it has one).\n\n"
        "A user message may include a block starting with '[Attached file: <name>]' — that is the real, "
        "already-extracted text of a file they just uploaded in this browser session (via OCR/text-extraction, "
        "never fabricated). Treat it as ground truth you have already read in full: answer questions about it "
        "directly, use it to fill in matching form fields when appropriate, and never claim you lack access to "
        "it or ask the user to go check the file themselves. When a document supplies field values, fill all of "
        "them in one update_form_fields call, giving the document's file name as each value's `source`, and "
        "then summarize briefly which boxes each document filled. If a document contradicts a value the user "
        "typed or one already in the form, don't silently overwrite it: point out the difference and ask which "
        "one is right. OCR can misread characters — if a document number fails its check (the form reports it "
        "as invalid), say so and ask the user to confirm it rather than guessing a correction."
    )
