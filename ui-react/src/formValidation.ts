/**
 * Client-side mirror of ai_circus_shared.form_validation — instant per-field
 * feedback and the Submit button's enabled state. The backend (form-agent's
 * POST /submissions/{slug}) is the final authority at submit time; this exists only
 * so the user doesn't have to round-trip to find out a field is missing/invalid.
 * Same rules, same messages (in the form's `locale`), same variant-aware field set.
 */
import type { FormConfig, FormFieldSpec, FormVariant } from "./apiClient";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_RE = /^\+?[0-9 ()-]{7,20}$/;
const NUMBER_RE = /^-?\d+([.,]\d+)?$/;
const CURRENCY_RE = /^-?\d+([.,]\d{1,2})?$/;
const IBAN_RE = /^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$/;
const DNI_RE = /^\d{8}[A-Z]$/;
const NIE_RE = /^[XYZ]\d{7}[A-Z]$/;
const NIF_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE";

type Messages = Record<
  "required" | "checkbox_required" | "email" | "phone" | "pattern" | "min_length" | "number" | "currency" | "iban" | "es_nif" | "option",
  string
>;

const MESSAGES: Record<"en" | "es", Messages> = {
  en: {
    required: "This field is required.",
    checkbox_required: "This box must be ticked.",
    email: "Must be a valid email address.",
    phone: "Must be a valid phone number.",
    pattern: "Does not match the required format.",
    min_length: "Must be at least {n} characters.",
    number: "Must be a number.",
    currency: "Must be an amount with at most 2 decimals.",
    iban: "Not a valid IBAN (check digits don't match).",
    es_nif: "Not a valid DNI/NIE (the control letter doesn't match).",
    option: "Must be one of the listed options.",
  },
  es: {
    required: "Casilla obligatoria.",
    checkbox_required: "Debe marcar esta casilla.",
    email: "Correo electrónico no válido.",
    phone: "Teléfono no válido.",
    pattern: "El formato no es correcto.",
    min_length: "Mínimo {n} caracteres.",
    number: "Debe ser un número.",
    currency: "Importe no válido (máximo 2 decimales).",
    iban: "IBAN no válido (los dígitos de control no cuadran).",
    es_nif: "DNI/NIE no válido (la letra de control no coincide).",
    option: "Debe ser una de las opciones de la lista.",
  },
};

export function normalizeCompact(value: string): string {
  return value.replace(/[\s.-]/g, "").toUpperCase();
}

/** ISO 13616: move the first four characters to the end, letters to 10-35, mod 97 == 1. */
export function ibanIsValid(value: string): boolean {
  const iban = normalizeCompact(value);
  if (!IBAN_RE.test(iban)) return false;
  const digits = (iban.slice(4) + iban.slice(0, 4)).replace(/[A-Z]/g, (ch) => String(ch.charCodeAt(0) - 55));
  let remainder = 0;
  for (const digit of digits) remainder = (remainder * 10 + Number(digit)) % 97;
  return remainder === 1;
}

/** A Spanish DNI (8 digits + letter) or NIE (X/Y/Z + 7 digits + letter) with the right control letter. */
export function esNifIsValid(value: string): boolean {
  let nif = normalizeCompact(value);
  if (NIE_RE.test(nif)) nif = String("XYZ".indexOf(nif[0])) + nif.slice(1);
  else if (!DNI_RE.test(nif)) return false;
  return NIF_LETTERS[Number(nif.slice(0, 8)) % 23] === nif[8];
}

export function isChecked(value: string | undefined): boolean {
  return (value ?? "").trim().toLowerCase() === "true";
}

function messages(form: FormConfig): Messages {
  return MESSAGES[form.locale ?? "en"] ?? MESSAGES.en;
}

/** Validate one non-empty field value against its type and `validation` rule.
 * Required-ness is `validateForm`'s job — an empty value is always valid here. */
export function validateField(spec: FormFieldSpec, value: string, msg: Messages = MESSAGES.en): string | null {
  if (!value) return null;
  if (spec.type === "number" && !NUMBER_RE.test(value)) return msg.number;
  if (spec.type === "currency" && !CURRENCY_RE.test(value)) return msg.currency;
  if (spec.type === "select" && spec.options?.length && !spec.options.includes(value)) return msg.option;
  if (spec.validation === "email" && !EMAIL_RE.test(value)) return msg.email;
  if (spec.validation === "phone" && !PHONE_RE.test(value)) return msg.phone;
  if (spec.validation === "pattern" && spec.pattern && !new RegExp(spec.pattern).test(value)) {
    return spec.helper_text ?? msg.pattern;
  }
  if (spec.validation === "min_length" && spec.min_length != null && value.length < spec.min_length) {
    return msg.min_length.replace("{n}", String(spec.min_length));
  }
  if (spec.validation === "iban" && !ibanIsValid(value)) return msg.iban;
  if (spec.validation === "es_nif" && !esNifIsValid(value)) return msg.es_nif;
  return null;
}

export function isRequired(spec: FormFieldSpec, values: Record<string, string>): boolean {
  if (spec.required) return true;
  if (spec.required_if) return spec.required_if.in_values.includes(values[spec.required_if.field] ?? "");
  return false;
}

/** The variant (specific model) the current values select, if any. */
export function selectedVariant(form: FormConfig, values: Record<string, string>): FormVariant | null {
  if (!form.classification_field) return null;
  const key = values[form.classification_field] ?? "";
  return (form.variants ?? []).find((v) => v.key === key) ?? null;
}

/** Shared fields plus the selected variant's own — what is validated and submitted. */
export function activeFields(form: FormConfig, values: Record<string, string>): FormFieldSpec[] {
  return [...form.fields, ...(selectedVariant(form, values)?.fields ?? [])];
}

/** Validate every active field of `form` against `values`. Returns `{field_id: message}`
 * for every missing-required or invalid field — empty means ready to submit. */
export function validateForm(form: FormConfig, values: Record<string, string>): Record<string, string> {
  const msg = messages(form);
  const errors: Record<string, string> = {};
  for (const spec of activeFields(form, values)) {
    const value = (values[spec.id] ?? "").trim();
    if (spec.type === "checkbox") {
      if (isRequired(spec, values) && !isChecked(value)) errors[spec.id] = msg.checkbox_required;
      continue;
    }
    if (!value) {
      if (isRequired(spec, values)) errors[spec.id] = msg.required;
      continue;
    }
    const error = validateField(spec, value, msg);
    if (error) errors[spec.id] = error;
  }
  return errors;
}
