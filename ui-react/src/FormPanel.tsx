import type { FormConfig, FormFieldSpec } from "./apiClient";

/** One field's editable input — driven entirely by `spec.type`/`spec.options`, the
 * same "generic renderer over a declarative schema" pattern predictUtils.tsx's
 * FeatureInput uses for tabular_ml's prediction form.
 */
function FieldInput({
  spec,
  value,
  onChange,
}: {
  spec: FormFieldSpec;
  value: string;
  onChange: (value: string) => void;
}) {
  if (spec.type === "select") {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="" disabled>
          Select…
        </option>
        {(spec.options ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }
  if (spec.type === "textarea") {
    return <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={4} />;
  }
  return <input type={spec.type} value={value} onChange={(e) => onChange(e.target.value)} />;
}

export function FormPanel({
  form,
  values,
  assistantFilled,
  errors,
  onChange,
  onSubmit,
  submitting,
  submitError,
  caseNumber,
}: {
  form: FormConfig;
  values: Record<string, string>;
  // Field ids most recently written by the assistant (via update_form_fields) —
  // still user-editable, just flagged so the user knows where that value came from.
  assistantFilled: ReadonlySet<string>;
  errors: Record<string, string>;
  onChange: (fieldId: string, value: string) => void;
  onSubmit: () => void;
  submitting: boolean;
  submitError: string | null;
  caseNumber: string | null;
}) {
  const missingOrInvalid = Object.entries(errors);

  return (
    <div className="assisted-form-panel">
      <h2>{form.title}</h2>

      {caseNumber ? (
        <div className="assisted-form-success">
          ✅ Submitted — case number <strong>{caseNumber}</strong>
        </div>
      ) : (
        <>
          {submitError && <p className="error">{submitError}</p>}
          {missingOrInvalid.length > 0 && (
            <div className="assisted-form-banner">
              <strong>Missing / invalid:</strong>
              <ul>
                {missingOrInvalid.map(([fieldId, message]) => {
                  const label = form.fields.find((f) => f.id === fieldId)?.label ?? fieldId;
                  return (
                    <li key={fieldId}>
                      {label}: {message}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          <div className="assisted-form-fields">
            {form.fields.map((spec) => (
              <label key={spec.id} className="assisted-form-field">
                <span className="assisted-form-field-label">
                  {spec.label}
                  {spec.required && <span className="assisted-form-required">*</span>}
                  {assistantFilled.has(spec.id) && <span className="assisted-form-badge">🤖 filled by assistant</span>}
                </span>
                <FieldInput spec={spec} value={values[spec.id] ?? ""} onChange={(v) => onChange(spec.id, v)} />
                {spec.helper_text && <span className="assisted-form-helper">{spec.helper_text}</span>}
                {errors[spec.id] && <span className="assisted-form-error">{errors[spec.id]}</span>}
              </label>
            ))}
          </div>

          <button className="btn-primary" onClick={onSubmit} disabled={submitting || missingOrInvalid.length > 0}>
            {submitting ? "Submitting…" : "Submit"}
          </button>
        </>
      )}
    </div>
  );
}
