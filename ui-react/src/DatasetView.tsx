import { useEffect, useMemo, useState } from "react";
import { datasetSample, type ScenarioSummary, type DatasetSample } from "./apiClient";
import { config } from "./config";
import { DatasetFilterPanel, type DatasetRow } from "./DatasetFilterPanel";
import { StatTile, Histogram, ScatterPlot, CategoryBars, colorScale } from "./charts";
import { exportJson } from "./predictUtils";

const DEFAULT_SAMPLE_LIMIT = 5000;
const SAMPLE_LIMIT_OPTIONS = [500, 1000, 5000, 10000, 20000];

type PlotMode = "histogram" | "counts" | "scatter";

/**
 * Pure data exploration — no model involved. Real rows from the same normalized
 * dataset training reads (see prediction's core/dataset.py), a client-side query/
 * filter builder, a small plot builder, and export. Model behavior/performance lives
 * in the Explore model tab instead, to keep this section "just the data".
 */
export function DatasetView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const featureColumns = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const featureSchema = useMemo(() => scenario.feature_schema ?? {}, [scenario.feature_schema]);
  const numericFeatures = useMemo(
    () => featureColumns.filter((f) => featureSchema[f]?.type === "numeric"),
    [featureColumns, featureSchema],
  );
  const categoricalFeatures = useMemo(
    () => featureColumns.filter((f) => featureSchema[f]?.type === "categorical"),
    [featureColumns, featureSchema],
  );

  // The target isn't a feature (never a model input), but it's still a real dataset
  // column returned by the sample endpoint — worth exploring alongside the features
  // it's a numeric target for regression scenarios, a class label for classification.
  const targetName = scenario.target ?? null;
  const targetIsNumeric = scenario.task_type === "regression";
  const labelFor = (f: string) => (f === targetName ? `${f} (target)` : f);

  const [sample, setSample] = useState<DatasetSample | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filtered, setFiltered] = useState<DatasetRow[]>([]);
  const [mode, setMode] = useState<PlotMode>(numericFeatures.length > 0 ? "histogram" : "counts");
  const [featureX, setFeatureX] = useState(numericFeatures[0] ?? categoricalFeatures[0] ?? "");
  const [featureY, setFeatureY] = useState(numericFeatures[1] ?? numericFeatures[0] ?? "");
  const [colorFeature, setColorFeature] = useState(categoricalFeatures[0] ?? "");
  const [sampleLimit, setSampleLimit] = useState(DEFAULT_SAMPLE_LIMIT);

  useEffect(() => {
    let cancelled = false;
    setSample(null);
    setError(null);
    datasetSample(config.predictionUrl, scenario.slug, sampleLimit, accessToken)
      .then((result) => {
        if (!cancelled) setSample(result);
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, [scenario.slug, sampleLimit, accessToken]);

  const targetOptions = useMemo(() => {
    if (!sample || !targetName || targetIsNumeric) return [];
    return [...new Set(sample.rows.map((r) => String(r[targetName])))];
  }, [sample, targetName, targetIsNumeric]);

  const numericFeaturesWithTarget = useMemo(
    () => (targetName && targetIsNumeric ? [...numericFeatures, targetName] : numericFeatures),
    [numericFeatures, targetName, targetIsNumeric],
  );
  const categoricalFeaturesWithTarget = useMemo(
    () => (targetName && !targetIsNumeric ? [...categoricalFeatures, targetName] : categoricalFeatures),
    [categoricalFeatures, targetName, targetIsNumeric],
  );

  const featureSchemaWithTarget = useMemo(() => {
    if (!sample || !targetName) return featureSchema;
    if (targetIsNumeric) {
      const values = sample.rows.map((r) => Number(r[targetName])).filter((v) => !Number.isNaN(v));
      if (values.length === 0) return featureSchema;
      const min = Math.min(...values);
      const max = Math.max(...values);
      return { ...featureSchema, [targetName]: { type: "numeric" as const, min, max, default: min } };
    }
    return { ...featureSchema, [targetName]: { type: "categorical" as const, options: targetOptions, default: targetOptions[0] ?? "" } };
  }, [sample, targetName, targetIsNumeric, targetOptions, featureSchema]);

  const numericSummary = useMemo(() => {
    if (!sample) return [];
    return numericFeaturesWithTarget.map((f) => {
      const values = sample.rows.map((r) => Number(r[f])).filter((v) => !Number.isNaN(v));
      // Math.min/max of an empty array is Infinity/-Infinity, not a missing value —
      // render "—" instead of those literal strings when a column has no numeric
      // values in this sample.
      if (values.length === 0) return { feature: f, min: null, max: null, mean: null };
      const mean = values.reduce((a, b) => a + b, 0) / values.length;
      return { feature: f, min: Math.min(...values), max: Math.max(...values), mean };
    });
  }, [sample, numericFeaturesWithTarget]);

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
                <td>{labelFor(s.feature)}</td>
                <td>{s.min === null ? "—" : s.min.toFixed(2)}</td>
                <td>{s.mean === null ? "—" : s.mean.toFixed(2)}</td>
                <td>{s.max === null ? "—" : s.max.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel-card">
        <h3>Query the data</h3>
        <DatasetFilterPanel
          featureColumns={[...featureColumns, ...(targetName ? [targetName] : [])]}
          featureSchema={featureSchemaWithTarget}
          labelFor={labelFor}
          rows={sample.rows}
          onFilteredChange={setFiltered}
        />
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
              {numericFeaturesWithTarget.length > 0 && <option value="histogram">Histogram (numeric)</option>}
              {categoricalFeaturesWithTarget.length > 0 && <option value="counts">Value counts (categorical)</option>}
              {numericFeaturesWithTarget.length > 1 && <option value="scatter">Scatter (numeric x numeric)</option>}
            </select>
          </label>
          {(mode === "histogram" || mode === "scatter") && (
            <label>
              X feature
              <select value={featureX} onChange={(e) => setFeatureX(e.target.value)}>
                {numericFeaturesWithTarget.map((f) => (
                  <option key={f} value={f}>
                    {labelFor(f)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {mode === "counts" && (
            <label>
              Feature
              <select value={featureX} onChange={(e) => setFeatureX(e.target.value)}>
                {categoricalFeaturesWithTarget.map((f) => (
                  <option key={f} value={f}>
                    {labelFor(f)}
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
                  {numericFeaturesWithTarget.map((f) => (
                    <option key={f} value={f}>
                      {labelFor(f)}
                    </option>
                  ))}
                </select>
              </label>
              {categoricalFeaturesWithTarget.length > 0 && (
                <label>
                  Color by
                  <select value={colorFeature} onChange={(e) => setColorFeature(e.target.value)}>
                    {categoricalFeaturesWithTarget.map((f) => (
                      <option key={f} value={f}>
                        {labelFor(f)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </>
          )}
        </div>

        {mode === "histogram" && featureX && (
          <Histogram values={filtered.map((r) => Number(r[featureX])).filter((v) => !Number.isNaN(v))} xLabel={labelFor(featureX)} />
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
          <ScatterConfigured rows={filtered} featureX={featureX} featureY={featureY} colorFeature={colorFeature} labelFor={labelFor} />
        )}
      </div>

      <div className="panel-card">
        <h3>Sample rows ({filtered.length} filtered)</h3>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                {sample.columns.map((c) => (
                  <th key={c}>{labelFor(c)}</th>
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
  labelFor,
}: {
  rows: DatasetRow[];
  featureX: string;
  featureY: string;
  colorFeature: string;
  labelFor: (f: string) => string;
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
  return <ScatterPlot points={points} xLabel={labelFor(featureX)} yLabel={labelFor(featureY)} refLine={false} sharedDomain={false} legend={legend} />;
}
