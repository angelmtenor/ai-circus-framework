import { useEffect, useMemo, useState } from "react";
import { datasetSample, type ScenarioSummary, type DatasetSample } from "./apiClient";
import { config } from "./config";
import { DatasetFilterPanel, type DatasetRow } from "./DatasetFilterPanel";
import { StatTile, Histogram, ScatterPlot, CategoryBars, colorScale } from "./charts";

const DEFAULT_SAMPLE_LIMIT = 5000;
const SAMPLE_LIMIT_OPTIONS = [500, 1000, 5000, 10000, 20000];

function exportJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

type PlotMode = "histogram" | "counts" | "scatter";

/**
 * Pure data exploration — no model involved. Real rows from the same normalized
 * dataset training reads (see prediction's core/dataset.py), a client-side query/
 * filter builder, a small plot builder, and export. Model behavior/performance lives
 * in the Explore model tab instead, to keep this section "just the data".
 */
export function DatasetView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const featureColumns = scenario.feature_columns ?? [];
  const featureSchema = scenario.feature_schema ?? {};
  const numericFeatures = featureColumns.filter((f) => featureSchema[f]?.type === "numeric");
  const categoricalFeatures = featureColumns.filter((f) => featureSchema[f]?.type === "categorical");

  const [sample, setSample] = useState<DatasetSample | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filtered, setFiltered] = useState<DatasetRow[]>([]);
  const [mode, setMode] = useState<PlotMode>(numericFeatures.length > 0 ? "histogram" : "counts");
  const [featureX, setFeatureX] = useState(numericFeatures[0] ?? categoricalFeatures[0] ?? "");
  const [featureY, setFeatureY] = useState(numericFeatures[1] ?? numericFeatures[0] ?? "");
  const [colorFeature, setColorFeature] = useState(categoricalFeatures[0] ?? "");
  const [sampleLimit, setSampleLimit] = useState(DEFAULT_SAMPLE_LIMIT);

  useEffect(() => {
    setSample(null);
    setError(null);
    datasetSample(config.predictionUrl, scenario.slug, sampleLimit, accessToken)
      .then(setSample)
      .catch((e) => setError((e as Error).message));
  }, [scenario.slug, sampleLimit, accessToken]);

  const numericSummary = useMemo(() => {
    if (!sample) return [];
    return numericFeatures.map((f) => {
      const values = sample.rows.map((r) => Number(r[f])).filter((v) => !Number.isNaN(v));
      const mean = values.reduce((a, b) => a + b, 0) / (values.length || 1);
      return { feature: f, min: Math.min(...values), max: Math.max(...values), mean };
    });
  }, [sample, numericFeatures]);

  if (error) {
    return (
      <div className="tab-panel">
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!sample) {
    return <div className="app-loading">Loading dataset…</div>;
  }

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>Data summary</h3>
        <div className="explore-controls">
          <label>
            Sample size
            <select value={sampleLimit} onChange={(e) => setSampleLimit(Number(e.target.value))}>
              {SAMPLE_LIMIT_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n.toLocaleString()} rows
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="kpi-row">
          <StatTile label="Total rows" value={String(sample.total_rows)} />
          <StatTile label="Sampled" value={String(sample.rows.length)} />
          <StatTile label="Numeric features" value={String(numericFeatures.length)} />
          <StatTile label="Categorical features" value={String(categoricalFeatures.length)} />
        </div>
        <p style={{ marginTop: "0.4rem", fontSize: "0.8rem", color: "var(--dim)" }}>
          Min/Mean/Max below, plus the Query and Plot panels, are computed from the {sample.rows.length.toLocaleString()}-row sample
          only — not the full {sample.total_rows.toLocaleString()}-row dataset. Increase the sample size for more representative stats.
        </p>
        <table className="data-table" style={{ marginTop: "0.75rem" }}>
          <thead>
            <tr>
              <th>Feature</th>
              <th>Min</th>
              <th>Mean</th>
              <th>Max</th>
            </tr>
          </thead>
          <tbody>
            {numericSummary.map((s) => (
              <tr key={s.feature}>
                <td>{s.feature}</td>
                <td>{s.min.toFixed(2)}</td>
                <td>{s.mean.toFixed(2)}</td>
                <td>{s.max.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel-card">
        <h3>Query the data</h3>
        <DatasetFilterPanel featureColumns={featureColumns} featureSchema={featureSchema} rows={sample.rows} onFilteredChange={setFiltered} />
        <button className="btn-secondary" onClick={() => exportJson(`${scenario.slug}-filtered.json`, filtered)} style={{ marginTop: "0.6rem" }}>
          ⬇ Export {filtered.length} filtered rows
        </button>
      </div>

      <div className="panel-card">
        <h3>Plot the data</h3>
        <div className="explore-controls">
          <label>
            Plot type
            <select value={mode} onChange={(e) => setMode(e.target.value as PlotMode)}>
              {numericFeatures.length > 0 && <option value="histogram">Histogram (numeric)</option>}
              {categoricalFeatures.length > 0 && <option value="counts">Value counts (categorical)</option>}
              {numericFeatures.length > 1 && <option value="scatter">Scatter (numeric x numeric)</option>}
            </select>
          </label>
          {(mode === "histogram" || mode === "scatter") && (
            <label>
              X feature
              <select value={featureX} onChange={(e) => setFeatureX(e.target.value)}>
                {numericFeatures.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </label>
          )}
          {mode === "counts" && (
            <label>
              Feature
              <select value={featureX} onChange={(e) => setFeatureX(e.target.value)}>
                {categoricalFeatures.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </label>
          )}
          {mode === "scatter" && (
            <>
              <label>
                Y feature
                <select value={featureY} onChange={(e) => setFeatureY(e.target.value)}>
                  {numericFeatures.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </label>
              {categoricalFeatures.length > 0 && (
                <label>
                  Color by
                  <select value={colorFeature} onChange={(e) => setColorFeature(e.target.value)}>
                    {categoricalFeatures.map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </>
          )}
        </div>

        {mode === "histogram" && featureX && (
          <Histogram values={filtered.map((r) => Number(r[featureX])).filter((v) => !Number.isNaN(v))} xLabel={featureX} />
        )}
        {mode === "counts" && featureX && (
          <CategoryBars
            items={Object.entries(
              filtered.reduce<Record<string, number>>((acc, r) => {
                const key = String(r[featureX]);
                acc[key] = (acc[key] ?? 0) + 1;
                return acc;
              }, {}),
            ).map(([category, score]) => ({ category, score }))}
            valueFormatter={(v) => String(v)}
          />
        )}
        {mode === "scatter" && featureX && featureY && (
          <ScatterConfigured rows={filtered} featureX={featureX} featureY={featureY} colorFeature={colorFeature} />
        )}
      </div>

      <div className="panel-card">
        <h3>Sample rows ({filtered.length} filtered)</h3>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                {sample.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 30).map((row, i) => (
                <tr key={i}>
                  {sample.columns.map((c) => (
                    <td key={c}>{String(row[c])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ScatterConfigured({
  rows,
  featureX,
  featureY,
  colorFeature,
}: {
  rows: DatasetRow[];
  featureX: string;
  featureY: string;
  colorFeature: string;
}) {
  const categories = colorFeature ? [...new Set(rows.map((r) => String(r[colorFeature])))] : [];
  const scale = colorScale(categories);
  const points = rows
    .map((r) => ({
      x: Number(r[featureX]),
      y: Number(r[featureY]),
      color: colorFeature ? scale.get(String(r[colorFeature])) : undefined,
    }))
    .filter((p) => !Number.isNaN(p.x) && !Number.isNaN(p.y));
  const legend = colorFeature ? categories.map((c) => ({ label: c, color: scale.get(c)! })) : undefined;
  return <ScatterPlot points={points} xLabel={featureX} yLabel={featureY} refLine={false} sharedDomain={false} legend={legend} />;
}
