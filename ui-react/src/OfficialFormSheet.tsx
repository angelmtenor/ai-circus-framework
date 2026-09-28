import { useEffect, useMemo, useRef, useState } from "react";
import type { FormConfig, FormFieldSpec, FormSection, FormVariant } from "./apiClient";
import { activeFields, isChecked, isRequired, selectedVariant } from "./formValidation";
import "./officialForm.css";

/**
 * The official, paper-like renderer for an assisted_form scenario whose form has
 * `sections` (see scenario_schema.FormConfig) — a real printed model on the desk: a
 * header with the issuing body's emblem and the model code, numbered section bands,
 * numbered boxes ("casillas") on a 12-column grid, ruled free-text areas, pen-mark
 * checkboxes, then the signature box and the box reserved for the Administration,
 * which receives the registry stamp once the form is filed. Laid out with the same
 * packing rule as form-agent's PDF (core/pdf.py), so the screen and the printed copy
 * match box for box.
 *
 * A family of models (`form.variants`) gets a model rail above the sheet: the shared
 * identification boxes stay, the selected model's own sections slot in between.
 * Everything is driven by the scenario's YAML — no scenario-specific code here.
 */

type Words = Record<
  | "choose"
  | "chooseHint"
  | "required"
  | "errors"
  | "draft"
  | "submit"
  | "submitting"
  | "receipt"
  | "filed"
  | "copy"
  | "signature"
  | "unsigned"
  | "signedBy"
  | "reserved"
  | "registry"
  | "entry"
  | "no"
  | "model"
  | "select"
  | "assistant"
  | "page"
  | "general"
  | "readFrom"
  | "conversation",
  string
>;

const WORDS: Record<"en" | "es", Words> = {
  es: {
    choose: "Seleccione un modelo",
    chooseHint: "Elija el modelo o cuéntele su trámite al asistente: él elegirá el adecuado.",
    required: "casillas obligatorias",
    errors: "por revisar",
    draft: "Borrador PDF",
    submit: "Firmar y presentar",
    submitting: "Presentando…",
    receipt: "Justificante PDF",
    filed: "Presentada · Nº de registro",
    copy: "Ejemplar para el interesado",
    signature: "Firma",
    unsigned: "Pendiente de firma electrónica",
    signedBy: "Firmado electrónicamente por",
    reserved: "Espacio reservado para la Administración",
    registry: "REGISTRO ELECTRÓNICO",
    entry: "ENTRADA",
    no: "Nº",
    model: "Modelo",
    select: "Seleccione…",
    assistant: "asistente",
    page: "Página 1",
    general: "general",
    readFrom: "Datos leídos de",
    conversation: "la conversación",
  },
  en: {
    choose: "Choose a form",
    chooseHint: "Pick the form, or tell the assistant what you need — it will pick the right one.",
    required: "required boxes",
    errors: "to fix",
    draft: "Draft PDF",
    submit: "Sign and submit",
    submitting: "Submitting…",
    receipt: "Receipt PDF",
    filed: "Submitted · registration no.",
    copy: "Applicant's copy",
    signature: "Signature",
    unsigned: "Awaiting electronic signature",
    signedBy: "Electronically signed by",
    reserved: "Reserved for the Administration",
    registry: "ELECTRONIC REGISTRY",
    entry: "RECEIVED",
    no: "No.",
    model: "Form",
    select: "Select…",
    assistant: "assistant",
    page: "Page 1",
    general: "general",
    readFrom: "Read from",
    conversation: "the conversation",
  },
};

const CIRCLED = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"];

function formatDate(iso: string, locale: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return locale === "es" ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

const DEFAULT_SPAN: Partial<Record<FormFieldSpec["type"], number>> = {
  textarea: 12,
  checkbox: 6,
  date: 3,
  number: 3,
  currency: 4,
};

/** Pack fields into 12-column rows; the last box of each row stretches to fill it
 * (form-agent's core/pdf.py `_rows` — keep the two in step). */
function packRows(fields: FormFieldSpec[]): { spec: FormFieldSpec; span: number }[][] {
  const rows: { spec: FormFieldSpec; span: number }[][] = [];
  let current: { spec: FormFieldSpec; span: number }[] = [];
  let used = 0;
  for (const spec of fields) {
    const span = spec.span ?? DEFAULT_SPAN[spec.type] ?? 6;
    if (current.length > 0 && used + span > 12) {
      rows.push(current);
      current = [];
      used = 0;
    }
    current.push({ spec, span });
    used += span;
  }
  if (current.length > 0) rows.push(current);
  for (const row of rows) row[row.length - 1].span += 12 - row.reduce((sum, b) => sum + b.span, 0);
  return rows;
}

/** (section, its active fields) in print order: shared "before", the variant's own, shared "after". */
function orderedSections(form: FormConfig, variant: FormVariant | null, fields: FormFieldSpec[]) {
  const shared = form.sections ?? [];
  const sections: FormSection[] = [
    ...shared.filter((s) => s.placement !== "after"),
    ...(variant?.sections ?? []),
    ...shared.filter((s) => s.placement === "after"),
  ];
  return sections
    .map((section) => ({
      section,
      fields: fields.filter((f) => f.section === section.id && f.id !== form.classification_field),
    }))
    .filter((s) => s.fields.length > 0);
}

function initials(name: string): string {
  const small = new Set(["de", "del", "la", "las", "los", "y", "of", "the", "and"]);
  return name
    .split(/\s+/)
    .filter((w) => w && !small.has(w.toLowerCase()))
    .map((w) => w[0])
    .join("")
    .slice(0, 3)
    .toUpperCase();
}

function Emblem({ name }: { name: string }) {
  return (
    <svg className="of-emblem" viewBox="0 0 64 64" aria-hidden="true">
      <circle cx="32" cy="32" r="31" className="of-emblem-disc" />
      <circle cx="32" cy="32" r="26.5" className="of-emblem-ring" />
      <circle cx="32" cy="32" r="23.5" className="of-emblem-ring of-emblem-ring--thin" />
      {Array.from({ length: 24 }, (_, i) => {
        const a = (i / 24) * Math.PI * 2;
        return <circle key={i} cx={32 + Math.cos(a) * 28.8} cy={32 + Math.sin(a) * 28.8} r="0.9" className="of-emblem-dot" />;
      })}
      <text x="32" y="37.5" textAnchor="middle" className="of-emblem-text">
        {initials(name)}
      </text>
    </svg>
  );
}

function ProgressRing({ done, total }: { done: number; total: number }) {
  const r = 15;
  const c = 2 * Math.PI * r;
  const share = total === 0 ? 1 : done / total;
  return (
    <svg className="of-ring" viewBox="0 0 40 40" aria-hidden="true">
      <circle cx="20" cy="20" r={r} className="of-ring-track" />
      <circle
        cx="20"
        cy="20"
        r={r}
        className={`of-ring-fill${share >= 1 ? " of-ring-fill--done" : ""}`}
        strokeDasharray={c}
        strokeDashoffset={c * (1 - share)}
      />
    </svg>
  );
}

function formatAmount(value: string, locale: string): string {
  const n = Number(value.replace(",", "."));
  if (!Number.isFinite(n) || value.trim() === "") return value;
  return n.toLocaleString(locale === "es" ? "es-ES" : "en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function Box({
  spec,
  span,
  value,
  error,
  showError,
  assisted,
  source,
  sourceNumber,
  fresh,
  locale,
  readOnly,
  words,
  missing,
  onChange,
}: {
  spec: FormFieldSpec;
  span: number;
  value: string;
  error: string | undefined;
  showError: boolean;
  assisted: boolean;
  source: string | undefined;
  sourceNumber: number | undefined;
  fresh: boolean;
  locale: "en" | "es";
  readOnly: boolean;
  words: Words;
  missing: boolean;
  onChange: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const classes = [
    "of-box",
    `of-box--${spec.type}`,
    assisted ? "of-box--assist" : "",
    fresh ? "of-box--fresh" : "",
    error && (showError || value) ? "of-box--error" : "",
    spec.validation === "iban" || spec.validation === "es_nif" ? "of-box--code" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const casilla = spec.casilla ? <span className="of-casilla">{spec.casilla}</span> : null;
  const tag = assisted ? (
    <span className={`of-src${sourceNumber === undefined ? " of-src--chat" : ""}`} title={`${words.readFrom} ${source ?? words.conversation}`}>
      {sourceNumber === undefined ? "✦" : (CIRCLED[sourceNumber] ?? sourceNumber + 1)}
    </span>
  ) : null;
  const errorLine = error && (showError || value) ? <span className="of-error">{error}</span> : null;

  if (spec.type === "checkbox") {
    const checked = isChecked(value);
    return (
      <div className={classes} style={{ gridColumn: `span ${span}` }} data-field={spec.id}>
        {casilla}
        <button
          type="button"
          role="checkbox"
          aria-checked={checked}
          className={`of-check${checked ? " of-check--on" : ""}`}
          onClick={() => !readOnly && onChange(checked ? "" : "true")}
          disabled={readOnly}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <rect x="1.5" y="1.5" width="17" height="17" rx="1.5" className="of-check-box" />
            {checked && (
              <>
                <path d="M5 5 L15 15" className="of-check-mark" />
                <path d="M15 5 L5 15" className="of-check-mark of-check-mark--2" />
              </>
            )}
          </svg>
          <span className="of-check-label">
            {spec.label}
            {missing && <span className="of-req" title="*" />}
            {tag}
          </span>
        </button>
        {errorLine}
      </div>
    );
  }

  let input;
  if (spec.type === "select") {
    input = (
      <select className="of-input" value={value} onChange={(e) => onChange(e.target.value)} disabled={readOnly}>
        <option value="" disabled>
          {words.select}
        </option>
        {(spec.options ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  } else if (spec.type === "textarea") {
    input = (
      <textarea
        className="of-input of-input--ruled"
        value={value}
        rows={Math.max(3, value.split("\n").length, Math.ceil(value.length / 90))}
        onChange={(e) => onChange(e.target.value)}
        readOnly={readOnly}
      />
    );
  } else if (spec.type === "currency" || spec.type === "number") {
    const shown = spec.type === "currency" && !editing ? formatAmount(value, locale) : value;
    input = (
      <span className="of-amount">
        <input
          className="of-input of-input--number"
          inputMode="decimal"
          value={shown}
          onFocus={() => setEditing(true)}
          onBlur={() => setEditing(false)}
          onChange={(e) => onChange(e.target.value)}
          readOnly={readOnly}
        />
        {spec.type === "currency" && <span className="of-currency">€</span>}
      </span>
    );
  } else if (spec.type === "date") {
    // The browser's date input formats by *browser* locale (09/28/2026 in en-US) — on
    // a Spanish form show dd/mm/aaaa, and switch to the native picker while editing.
    const native = editing || !value || locale !== "es";
    input = (
      <input
        className="of-input"
        type={native ? "date" : "text"}
        value={native ? value : formatDate(value, locale)}
        onFocus={() => setEditing(true)}
        onBlur={() => setEditing(false)}
        onChange={(e) => onChange(e.target.value)}
        readOnly={readOnly}
      />
    );
  } else {
    input = (
      <input
        className="of-input"
        type={spec.type === "email" ? "email" : spec.type === "tel" ? "tel" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        readOnly={readOnly}
        spellCheck={false}
      />
    );
  }
  return (
    <label className={classes} style={{ gridColumn: `span ${span}` }} data-field={spec.id}>
      <span className="of-label">
        <span className="of-label-text">{spec.label}</span>
        {missing && <span className="of-req" title="*" />}
        {tag}
      </span>
      {casilla}
      {input}
      {errorLine}
    </label>
  );
}

function Stamp({ caseNumber, when, issuer, words }: { caseNumber: string; when: Date; issuer: string; words: Words }) {
  const stamp = when.toLocaleString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  return (
    <svg className="of-stamp" viewBox="0 0 260 96" role="img" aria-label={`${words.registry} ${caseNumber}`}>
      <defs>
        <filter id="of-ink" x="-5%" y="-5%" width="110%" height="110%">
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="7" result="noise" />
          <feDisplacementMap in="SourceGraphic" in2="noise" scale="1.6" />
        </filter>
      </defs>
      <g filter="url(#of-ink)">
        <rect x="3" y="3" width="254" height="90" rx="10" className="of-stamp-line of-stamp-line--thick" />
        <rect x="9" y="9" width="242" height="78" rx="7" className="of-stamp-line" />
        <text x="130" y="31" textAnchor="middle" className="of-stamp-title">
          {words.registry}
        </text>
        <text x="130" y="49" textAnchor="middle" className="of-stamp-sub">
          {words.entry} · {stamp}
        </text>
        <text x="130" y="67" textAnchor="middle" className="of-stamp-no">
          {words.no} {caseNumber}
        </text>
        <text x="130" y="81" textAnchor="middle" className="of-stamp-issuer">
          {issuer}
        </text>
      </g>
    </svg>
  );
}

export function OfficialFormSheet({
  form,
  values,
  errors,
  assistantFilled,
  sources,
  fresh,
  onChange,
  onSubmit,
  submitting,
  submitError,
  caseNumber,
  submittedAt,
  onDraftPdf,
  onReceiptPdf,
  pdfBusy,
}: {
  form: FormConfig;
  values: Record<string, string>;
  errors: Record<string, string>;
  assistantFilled: ReadonlySet<string>;
  sources: Record<string, string>;
  // Field ids the assistant wrote in its latest update — highlighted for a moment.
  fresh: ReadonlySet<string>;
  onChange: (fieldId: string, value: string) => void;
  onSubmit: () => void;
  submitting: boolean;
  submitError: string | null;
  caseNumber: string | null;
  submittedAt: Date | null;
  onDraftPdf: () => void;
  onReceiptPdf: () => void;
  pdfBusy: boolean;
}) {
  const locale = form.locale ?? "en";
  const words = WORDS[locale];
  const sheetRef = useRef<HTMLDivElement>(null);
  const [showAllErrors, setShowAllErrors] = useState(false);
  const variant = selectedVariant(form, values);
  const fields = useMemo(() => activeFields(form, values), [form, values]);
  const sections = useMemo(() => orderedSections(form, variant, fields), [form, variant, fields]);
  const readOnly = caseNumber !== null;

  const required = fields.filter((f) => f.id !== form.classification_field && isRequired(f, values));
  const requiredDone = required.filter((f) => !errors[f.id]).length;
  const errorCount = Object.keys(errors).filter((id) => id !== form.classification_field).length;
  const applicant = (form.applicant_fields ?? []).map((id) => values[id]?.trim()).filter(Boolean).join(" ");

  // Scroll the sheet's own container (never the page — scrollIntoView would also
  // scroll the window, tucking the model rail under the app header).
  function scrollToBox(selector: string) {
    const container = sheetRef.current;
    const box = container?.querySelector<HTMLElement>(selector);
    if (!container || !box) return;
    const offset = box.getBoundingClientRect().top - container.getBoundingClientRect().top;
    container.scrollTo({ top: container.scrollTop + offset - container.clientHeight / 2 + box.clientHeight / 2, behavior: "smooth" });
  }

  // Watch it fill: bring the first box the assistant just wrote into view.
  useEffect(() => {
    if (fresh.size > 0) scrollToBox(".of-box--fresh");
  }, [fresh]);

  useEffect(() => setShowAllErrors(false), [variant?.key]);

  function trySubmit() {
    if (errorCount > 0) {
      setShowAllErrors(true);
      requestAnimationFrame(() => scrollToBox(".of-box--error"));
      return;
    }
    onSubmit();
  }

  // Each document the assistant read gets a number, in the order it was first used.
  const documents = useMemo(() => [...new Set(Object.values(sources))], [sources]);
  const code = variant?.code ?? form.code ?? "";
  const [codeLabel, codeNumber] = /^(modelo|form)\s+/i.test(code) ? [code.split(/\s+/)[0], code.split(/\s+/).slice(1).join(" ")] : [words.model, code];
  let sectionNumber = 0;

  return (
    <div className="of-desk">
      {(form.variants ?? []).length > 0 && (
        <nav className="of-rail" aria-label={words.model}>
          {(form.variants ?? []).map((v) => {
            const active = v.key === variant?.key;
            const byAssistant = active && form.classification_field ? assistantFilled.has(form.classification_field) : false;
            return (
              <button
                key={v.key}
                type="button"
                className={`of-rail-item${active ? " of-rail-item--active" : ""}`}
                onClick={() => !readOnly && form.classification_field && onChange(form.classification_field, v.key)}
                disabled={readOnly && !active}
                title={v.summary ?? v.title}
              >
                <span className="of-rail-code">
                  {v.code.replace(/^(modelo|form)\s+/i, "")}
                  {v.key === form.general_variant && <em>{words.general}</em>}
                  {byAssistant && <span className="of-rail-bot">✦</span>}
                </span>
                <span className="of-rail-title">{v.title}</span>
              </button>
            );
          })}
        </nav>
      )}

      <div className="of-status">
        {readOnly ? (
          <span className="of-status-filed">
            <span className="of-status-seal">✓</span>
            {words.filed} <b>{caseNumber}</b>
          </span>
        ) : (
          <>
            <ProgressRing done={requiredDone} total={required.length} />
            <span className="of-status-count">
              <b>
                {requiredDone}/{required.length}
              </b>{" "}
              {words.required}
            </span>
            {errorCount > 0 && (
              <button type="button" className="of-status-errors" onClick={trySubmit} disabled={!variant && (form.variants ?? []).length > 0}>
                {errorCount} {words.errors}
              </button>
            )}
          </>
        )}
        <span className="of-status-spacer" />
        {readOnly ? (
          <button type="button" className="of-btn of-btn--ghost" onClick={onReceiptPdf} disabled={pdfBusy}>
            ⤓ {words.receipt}
          </button>
        ) : (
          <>
            <button type="button" className="of-btn of-btn--ghost" onClick={onDraftPdf} disabled={pdfBusy}>
              ⤓ {words.draft}
            </button>
            <button
              type="button"
              className={`of-btn of-btn--primary${errorCount === 0 ? " of-btn--ready" : ""}`}
              onClick={trySubmit}
              disabled={submitting || (!variant && (form.variants ?? []).length > 0)}
            >
              ✒ {submitting ? words.submitting : words.submit}
            </button>
          </>
        )}
      </div>
      {submitError && <p className="of-submit-error">{submitError}</p>}
      {assistantFilled.size > 0 && (
        <div className="of-docs">
          <span className="of-docs-label">{words.readFrom}</span>
          {documents.map((doc, i) => (
            <span key={doc} className="of-doc">
              <span className="of-src">{CIRCLED[i] ?? i + 1}</span>
              {doc}
            </span>
          ))}
          <span className="of-doc">
            <span className="of-src of-src--chat">✦</span>
            {words.conversation}
          </span>
        </div>
      )}

      <div className="of-scroll" ref={sheetRef}>
        <article className={`of-sheet${readOnly ? " of-sheet--filed" : ""}`} lang={locale}>
          <header className="of-head">
            <div className="of-head-issuer">
              {form.issuer && <Emblem name={form.issuer} />}
              <div>
                <div className="of-issuer">{form.issuer ?? form.title}</div>
                {form.issuer_unit && <div className="of-issuer-unit">{form.issuer_unit}</div>}
              </div>
            </div>
            <div className="of-code">
              <span className="of-copy">{words.copy}</span>
              <span className="of-code-label">{codeLabel}</span>
              <span className={`of-code-number${variant ? "" : " of-code-number--empty"}`}>{variant ? codeNumber : "—"}</span>
            </div>
          </header>
          <h3 className="of-title" key={variant?.key ?? "none"}>
            {variant?.title ?? (form.variants?.length ? words.choose : form.title)}
          </h3>
          {!variant && (form.variants ?? []).length > 0 && <p className="of-hint">{words.chooseHint}</p>}

          {sections.map(({ section, fields: sectionFields }) => {
            sectionNumber += 1;
            const complete = sectionFields.every((f) => !errors[f.id]) && sectionFields.some((f) => values[f.id]?.trim());
            return (
              <section key={section.id} className="of-section">
                <div className="of-band">
                  <span className="of-band-number">{sectionNumber}</span>
                  <span className="of-band-title">{section.title}</span>
                  {complete && <span className="of-band-done">✓</span>}
                </div>
                {section.note && <p className="of-note">{section.note}</p>}
                <div className="of-grid">
                  {packRows(sectionFields).flatMap((row) =>
                    row.map(({ spec, span }) => (
                      <Box
                        key={spec.id}
                        spec={spec}
                        span={span}
                        value={values[spec.id] ?? ""}
                        error={errors[spec.id]}
                        showError={showAllErrors}
                        assisted={assistantFilled.has(spec.id)}
                        source={sources[spec.id]}
                        sourceNumber={sources[spec.id] ? documents.indexOf(sources[spec.id]) : undefined}
                        fresh={fresh.has(spec.id)}
                        locale={locale}
                        readOnly={readOnly}
                        words={words}
                        missing={!readOnly && Boolean(errors[spec.id]) && !values[spec.id]?.trim() && spec.type !== "checkbox"}
                        onChange={(v) => onChange(spec.id, v)}
                      />
                    )),
                  )}
                </div>
              </section>
            );
          })}

          <div className="of-closing">
            <div className="of-sign">
              <span className="of-label">{words.signature}</span>
              {readOnly && submittedAt ? (
                <>
                  <svg className="of-signature" viewBox="0 0 220 60" aria-hidden="true">
                    <path d="M8 40 C 30 8, 44 58, 70 30 S 104 12, 118 34 S 150 50, 170 22 S 200 26, 212 30" />
                  </svg>
                  <span className="of-signed">
                    {words.signedBy} {applicant || "—"} ·{" "}
                    {submittedAt.toLocaleString(locale === "es" ? "es-ES" : "en-GB", { dateStyle: "short", timeStyle: "short" })}
                  </span>
                </>
              ) : (
                <span className="of-unsigned">{words.unsigned}</span>
              )}
            </div>
            <div className="of-reserved">
              <span className="of-label">{words.reserved}</span>
              {readOnly && submittedAt && caseNumber && (
                <Stamp caseNumber={caseNumber} when={submittedAt} issuer={form.issuer ?? form.title} words={words} />
              )}
            </div>
          </div>
          <footer className="of-foot">
            <span>{[form.issuer, form.issuer_unit].filter(Boolean).join(" · ") || form.title}</span>
            <span>{words.page}</span>
          </footer>
        </article>
      </div>
    </div>
  );
}
