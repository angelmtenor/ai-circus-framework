/**
 * Client-side mirror of ai_circus_shared.form_validation — instant per-field
 * feedback and the Submit button's enabled state. The backend (form-agent's
 * POST /submissions/{slug}) is the final authority at submit time; this exists only
 * so the user doesn't have to round-trip to find out a field is missing/invalid.
 */
import type { FormConfig, FormFieldSpec } from "./apiClient";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_RE = /^\+?[0-9 ()-]{7,20}$/;

/** Validate one non-empty field value against its `validation` rule. Required-ness
 * is `validateForm`'s job, not this function's — an empty value is always valid here.
 */
export function validateField(spec: FormFieldSpec, value: string): string | null {
  if (!value) return null;
  if (spec.validation === "email" && !EMAIL_RE.test(value)) return "Must be a valid email address.";
  if (spec.validation === "phone" && !PHONE_RE.test(value)) return "Must be a valid phone number.";
  if (spec.validation === "pattern" && spec.pattern && !new RegExp(spec.pattern).test(value)) {
    return spec.helper_text ?? "Does not match the required format.";
  }
  if (spec.validation === "min_length" && spec.min_length != null && value.length < spec.min_length) {
    return `Must be at least ${spec.min_length} characters.`;
  }
  return null;
}

function isRequired(spec: FormFieldSpec, values: Record<string, string>): boolean {
  if (spec.required) return true;
  if (spec.required_if) return spec.required_if.in_values.includes(values[spec.required_if.field] ?? "");
  return false;
}

/** Validate every field in `form` against `values`. Returns `{field_id: message}` for
 * every missing-required or invalid field — an empty object means the form is ready
 * to submit.
 */
export function validateForm(form: FormConfig, values: Record<string, string>): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const spec of form.fields) {
    const value = (values[spec.id] ?? "").trim();
    if (!value) {
      if (isRequired(spec, values)) errors[spec.id] = "This field is required.";
      continue;
    }
    const error = validateField(spec, value);
    if (error) errors[spec.id] = error;
  }
  return errors;
}
