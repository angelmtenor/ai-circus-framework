/**
 * An assisted_form scenario's second tab (`ui_extras: {kind: case_tracker}`): the signed-in user's
 * own filed cases, from form-agent's GET /submissions/{slug}. Plain DOM with nothing running: it
 * fetches when opened and only re-fetches on Refresh. There is no case workflow behind a filing, so
 * it shows what is real (when, the receipt, the values, the filed PDF) and no invented status.
 */
import { useEffect, useState } from "react";
import { filedCases, submissionPdf, type CaseTrackerExtra, type FiledCases, type ScenarioSummary } from "./apiClient";
import { PdfPreviewOverlay, type PdfPreview } from "./AssistedFormView";
import { config } from "./config";
import "./caseTracker.css";

export function CaseTrackerView({ scenario, extras, accessToken }: { scenario: ScenarioSummary; extras: CaseTrackerExtra; accessToken: string | null }) {
  const [data, setData] = useState<FiledCases | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [openCase, setOpenCase] = useState<string | null>(null);
  const [preview, setPreview] = useState<PdfPreview | null>(null);
  const form = scenario.form;
  const locale = form?.locale === "es" ? "es-ES" : undefined;

  useEffect(() => {
    let live = true;
    filedCases(config.formAgentUrl, scenario.slug, accessToken)
      .then((d) => {
        if (!live) return;
        setData(d);
        setError(null);
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [scenario.slug, accessToken, refresh]);

  function showPdf(caseNumber: string) {
    submissionPdf(config.formAgentUrl, scenario.slug, caseNumber, accessToken)
      .then((blob) => setPreview({ url: URL.createObjectURL(blob), filename: `${caseNumber}.pdf`, title: caseNumber }))
      .catch((e: Error) => setError(e.message));
  }

  function closePreview() {
    if (preview) URL.revokeObjectURL(preview.url);
    setPreview(null);
  }

  const labelOf = (id: string) => form?.fields.find((f) => f.id === id)?.label ?? id;
  const kindOf = (fields: Record<string, string>) => (form?.classification_field ? fields[form.classification_field] : undefined);

  return (
    <div className="tab-panel cases">
      <div className="cases-head">
        <h2>{extras.title}</h2>
        <button className="btn-secondary" aria-label="Refresh" onClick={() => setRefresh((n) => n + 1)}>
          ↻
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="panel-hint">…</p>}
      {data?.cases.length === 0 && <p className="panel-card panel-hint">{extras.empty_text}</p>}
      <div className="cases-list">
        {data?.cases.map((c) => {
          const open = openCase === c.case_number;
          const kind = kindOf(c.fields);
          return (
            <div key={c.case_number} className="panel-card cases-card">
              <div className="cases-card-head">
                <code>{c.case_number}</code>
                {kind && <span className="scenario-meta-pill">{kind}</span>}
                <span className="cases-when">
                  {extras.filed_label}
                  {c.submitted_at ? ` · ${new Date(c.submitted_at).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" })}` : ""}
                </span>
              </div>
              {c.verification_code && (
                <p className="panel-hint">
                  {extras.receipt_label}: <code>{c.verification_code}</code>
                </p>
              )}
              <div className="cases-actions">
                <button className="btn-secondary" aria-expanded={open} onClick={() => setOpenCase(open ? null : c.case_number)}>
                  {extras.details_label}
                </button>
                <button className="btn-secondary" onClick={() => showPdf(c.case_number)}>
                  {extras.open_pdf_label}
                </button>
              </div>
              {open && (
                <dl className="cases-fields">
                  {Object.entries(c.fields).map(([id, value]) => (
                    <div key={id}>
                      <dt>{labelOf(id)}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          );
        })}
      </div>
      {data?.truncated && <p className="panel-hint">{extras.truncated_text}</p>}
      {preview && <PdfPreviewOverlay preview={preview} onClose={closePreview} />}
    </div>
  );
}
