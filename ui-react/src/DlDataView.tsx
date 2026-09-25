import { useMemo, useState } from "react";
import type { ScenarioSummary } from "./apiClient";
import { BarList } from "./charts";
import { DlModelUnavailable, ImageLightbox, labelName, labelsOf, pct, Thumbnail, topOf, useDlSamples, probText } from "./dlShared";

const PAGE = 48;

/**
 * The published held-out samples (never the training set) — an image gallery or a
 * list of patient messages — with their true class, the deployed model's own
 * prediction, and a class-mix chart. Filterable by class and by "model got it wrong",
 * so a user can go straight to the interesting failures.
 */
export function DlDataView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const samples = useDlSamples(scenario.slug, accessToken);
  const [label, setLabel] = useState<string>("all");
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [page, setPage] = useState(1);
  const [enlarged, setEnlarged] = useState<number | null>(null);
  const isImage = scenario.deep_learning?.modality === "image";
  const labels = labelsOf(scenario);

  const rows = useMemo(
    () =>
      (samples.data ?? [])
        .map((s) => ({ ...s, top: topOf(scenario, s.probs) }))
        .filter((s) => label === "all" || s.label === label)
        .filter((s) => !onlyErrors || s.top.key !== s.label),
    [samples.data, scenario, label, onlyErrors],
  );
  const mix = useMemo(() => {
    const counts = new Map<string, number>();
    (samples.data ?? []).forEach((s) => counts.set(s.label, (counts.get(s.label) ?? 0) + 1));
    return labels.map((l) => ({ label: l.label, value: counts.get(l.key) ?? 0 }));
  }, [samples.data, labels]);

  if (samples.error) return <DlModelUnavailable error={samples.error} />;
  if (!samples.data) return <div className="app-loading">Loading samples…</div>;
  const correct = samples.data.filter((s) => topOf(scenario, s.probs).key === s.label).length;

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>{isImage ? "Held-out image gallery" : "Held-out patient messages"}</h3>
        <p className="panel-hint" style={{ marginTop: "-0.2rem" }}>
          {samples.data.length} samples from the test split the model never trained on — each with its expert label and the
          deployed model's prediction ({pct(correct / samples.data.length)} correct here).
          {isImage && " Click any image to enlarge it (with the model's heatmap on demand)."}
        </p>
        <div className="dl-toolbar">
          <label>
            Class{" "}
            <select value={label} onChange={(e) => (setLabel(e.target.value), setPage(1))}>
              <option value="all">All ({samples.data.length})</option>
              {labels.map((l) => (
                <option key={l.key} value={l.key}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
          <label className="dl-check">
            <input type="checkbox" checked={onlyErrors} onChange={(e) => (setOnlyErrors(e.target.checked), setPage(1))} /> Only
            the model's mistakes
          </label>
          <span className="panel-hint">{rows.length} shown</span>
        </div>

        {enlarged !== null && (
          <ImageLightbox
            scenario={scenario}
            items={rows}
            index={enlarged}
            onIndex={setEnlarged}
            onClose={() => setEnlarged(null)}
            accessToken={accessToken}
          />
        )}
        {isImage ? (
          <div className="dl-gallery">
            {rows.slice(0, page * PAGE).map((s, i) => (
              <figure key={s.id} className={`dl-gallery-item${s.top.key !== s.label ? " dl-gallery-item--wrong" : ""}`}>
                <Thumbnail slug={scenario.slug} sampleId={s.id} accessToken={accessToken} size={132} onClick={() => setEnlarged(i)} />
                <figcaption>
                  <span>{labelName(scenario, s.label)}</span>
                  <span className="panel-hint">
                    AI: {labelName(scenario, s.top.key)} {probText(s.top.confidence)}
                  </span>
                </figcaption>
              </figure>
            ))}
          </div>
        ) : (
          <div className="table-scroll">
            <table className="data-table dl-text-table">
              <colgroup>
                <col />
                <col style={{ width: "22%" }} />
                <col style={{ width: "22%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Patient message</th>
                  <th>Label</th>
                  <th>Model</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, page * PAGE).map((s) => (
                  <tr key={s.id} className={s.top.key !== s.label ? "dl-row-wrong" : ""}>
                    <td>{s.text}</td>
                    <td>{labelName(scenario, s.label)}</td>
                    <td>
                      {labelName(scenario, s.top.key)} <span className="panel-hint">{probText(s.top.confidence)}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > page * PAGE && (
          <button className="btn-secondary" onClick={() => setPage((p) => p + 1)} style={{ marginTop: "0.8rem" }}>
            Show more
          </button>
        )}
      </div>

      <div className="panel-card">
        <h3>Class mix</h3>
        <BarList items={mix} signed={false} valueFormatter={(v) => String(Math.round(v))} />
      </div>
    </div>
  );
}
