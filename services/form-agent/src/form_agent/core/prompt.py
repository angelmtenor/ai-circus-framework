"""
- Title:    Form-filling grounding (system prompt construction)
- Author:   ai-circus-framework contributors

Entirely data-driven from `definition.form`/`definition.chat.context` — no
scenario-specific wording is baked in here, so the same function grounds any
`assisted_form` scenario, classification-driven or not.
"""

from __future__ import annotations

from ai_circus_shared.scenario_schema import FormFieldSpec, ScenarioDefinition


def _describe_field(spec: FormFieldSpec) -> str:
    """One line describing a field for the system prompt — id, label, and what makes
    it required, so the model knows exactly when it can stop asking about it.
    """
    if spec.required:
        requirement = "always required"
    elif spec.required_if is not None:
        requirement = f"required only if {spec.required_if.field!r} is one of {spec.required_if.in_values}"
    else:
        requirement = "optional"
    bits = [f"id={spec.id!r}", f"label={spec.label!r}", f"type={spec.type}", requirement]
    if spec.options:
        bits.append(f"options={spec.options}")
    if spec.helper_text:
        bits.append(f"hint={spec.helper_text!r}")
    return "- " + ", ".join(bits)


def build_form_system_prompt(definition: ScenarioDefinition) -> str:
    """Ground the assistant in the scenario's chat.context and its form's field catalog."""
    form = definition.form
    assert form is not None  # guaranteed by kind="assisted_form" filter
    fields_description = "\n".join(_describe_field(f) for f in form.fields)

    classification_phrase = ""
    if form.classification_field is not None:
        classification_phrase = (
            f"\n\nThe field {form.classification_field!r} categorizes the request. Call the retrieve_catalog "
            f"tool to figure out which of these types applies, based on what the user describes, before "
            f"setting it: {form.classification_options}. If you're not confident yet, ask a clarifying "
            "question instead of guessing."
        )

    return (
        f"You are a form-filling assistant for '{form.title}'.\n"
        f"{definition.chat.context.strip()}\n\n"
        f"The form has these fields:\n{fields_description}"
        f"{classification_phrase}\n\n"
        "Whenever you learn or confirm a field's value from the conversation, call the update_form_fields tool "
        "right away with every field you now know — don't wait until the end of the conversation, and don't "
        "wait to be asked. Never invent a value the user didn't actually state (a name, ID number, phone, or "
        "email you're not sure about is worse than leaving it blank).\n\n"
        "You'll also be told the form's current values and which required fields are still missing or invalid "
        "(as context, not something the user typed) — use that to avoid re-asking about fields that are already "
        "filled in correctly, and to explain concretely what's still needed when the user asks why they can't "
        "submit yet."
    )
