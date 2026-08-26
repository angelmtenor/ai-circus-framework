import { useEffect, useMemo, useState } from "react";
import { datasetSample, type ScenarioSummary, type ChartType, type ChartAgg, type DatasetSample } from "./apiClient";
import { config, MAX_ROWS } from "./config";
import { DatasetFilterPanel, type DatasetRow } from "./DatasetFilterPanel";
import { StatTile } from "./charts";
import { PlotlyChart } from "./PlotlyChart";
import { buildChart, specToChartCardConfig, defaultChartCardConfig, type ChartCardConfig } from "./chartBuilder";
import { useTheme } from "./useTheme";
import { Icon } from "./Icon";
import { exportJson, featureLabel } from "./predictUtils";

const DEFAULT_SAMPLE_LIMIT = 5000;
const SAMPLE_LIMIT_OPTIONS = [500, 1000, 5000, MAX_ROWS];

const CHART_TYPE_OPTIONS: { value: ChartType; label: string }[] = [
  { value: "histogram", label: "Histogram" },
  { value: "bar", label: "Bar" },
  { value: "line", label: "Line" },
  { value: "scatter", label: "Scatter" },
  { value: "scatter3d", label: "Scatter 3D" },
  { value: "box", label: "Box" },
  { value: "pie", label: "Pie" },
  { value: "heatmap", label: "Heatmap / correlation" },
];

const AGG_OPTIONS: { value: ChartAgg; label: string }[] = [
  { value: "count", label: "count" },
  { value: "sum", label: "sum" },
  { value: "mean", label: "mean" },
  { value: "min", label: "min" },
  { value: "max", label: "max" },
];

/**
 * Pure data exploration — no model involved. Real rows from the same normalized
 * dataset training reads (see prediction's core/dataset.py), a client-side query/
 * filter builder, and a Power-BI-style multi-chart dashboard (type/X/Y/Z/color-by/
 * aggregate per card, seeded from the scenario's YAML `default_charts` — see
 * chartBuilder.ts). Model behavior/performance lives in the Explore model tab
 * instead, to keep this tab "just the data".
 */
export function DataView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const { theme } = useTheme();
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
  const labelFor = (f: string) =>
    f === targetName ? `${scenario.target_label ?? targetName} (target)` : featureLabel(scenario, f);

  const [sample, setSample] = useState<DatasetSample | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filtered, setFiltered] = useState<DatasetRow[]>([]);
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
  const allFeaturesWithTarget = useMemo(
    () => [...numericFeaturesWithTarget, ...categoricalFeaturesWithTarget],
    [numericFeaturesWithTarget, categoricalFeaturesWithTarget],
  );

  const featureSchemaWithTarget = useMemo(() => {
    if (!sample || !targetName) return featureSchema;
    if (targetIsNumeric) {
      const values = sample.rows.map((r) => Number(r[targetName])).filter((v) => !Number.isNaN(v));
      if (values.length === 0) return featureSchema;
      const min = Math.min(...values);
      const max = Math.max(...values);
      return {
        ...featureSchema,
        [targetName]: { type: "numeric" as const, min, max, default: min, label: scenario.target_label ?? targetName },
      };
    }
    return {
      ...featureSchema,
      [targetName]: {
        type: "categorical" as const,
        options: targetOptions,
        default: targetOptions[0] ?? "",
        label: scenario.target_label ?? targetName,
      },
    };
  }, [sample, targetName, targetIsNumeric, targetOptions, featureSchema, scenario.target_label]);

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

  // Seeded from the scenario's YAML `default_charts` (see chartBuilder.ts); falls
  // back to a single generic chart when a scenario defines none. Re-seeds whenever
  // the scenario itself changes, but stays put across sample-size/filter changes so
  // in-progress edits to a card aren't lost.
  const [charts, setCharts] = useState<ChartCardConfig[]>(() => initialCharts());
  function initialCharts(): ChartCardConfig[] {
    if (scenario.default_charts && scenario.default_charts.length > 0) {
      return scenario.default_charts.map(specToChartCardConfig);
    }
    return [defaultChartCardConfig(numericFeaturesWithTarget, categoricalFeaturesWithTarget)];
  }
  useEffect(() => {
    setCharts(initialCharts());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenario.slug]);

  function updateChart(id: string, patch: Partial<ChartCardConfig>) {
    setCharts((cs) => cs.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  }
  function removeChart(id: string) {
    setCharts((cs) => cs.filter((c) => c.id !== id));
  }
  function addChart() {
    setCharts((cs) => [...cs, defaultChartCardConfig(numericFeaturesWithTarget, categoricalFeaturesWithTarget)]);
  }

  // Display-only relabeling of the target column's raw value (e.g. "0"/"1" ->
  // "Stayed"/"Churned") for the sample-rows table below — scoped to just that one
  // cell. targetOptions/buildChart/aggregation must keep reading raw values (some
  // scenarios' default_charts compute e.g. `agg: mean` directly over the raw 0/1
  // target to get a rate), so this never touches the underlying row data.
  function displayValue(column: string, value: string | number | boolean | null): string {
    if (column === targetName && scenario.target_value_labels) {
      return scenario.target_value_labels[String(value)] ?? String(value);
    }
    return String(value);
  }

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
        <p style={{ marginTop: "-0.2rem", marginBottom: "0.6rem" }}>{scenario.description}</p>
        {scenario.credits && (
          <p className="panel-hint" style={{ marginTop: "-0.2rem" }}>
            Dataset credit: {scenario.credits.source} —{" "}
            <a href={scenario.credits.url} target="_blank" rel="noreferrer">
              original source
            </a>
            {scenario.credits.note ? ` (${scenario.credits.note})` : ""}
          </p>
        )}
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
        {sample.total_rows > sample.rows.length && (
          <p style={{ marginTop: "0.4rem", fontSize: "0.8rem", color: "var(--dim)" }}>
            Min/Mean/Max below, plus the Query and chart panels, are computed from the {sample.rows.length.toLocaleString()}-row
            sample only — not the full {sample.total_rows.toLocaleString()}-row dataset. Increase the sample size for more
            representative stats (up to {MAX_ROWS.toLocaleString()}).
          </p>
        )}
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
          <Icon name="download" size={14} /> Export {filtered.length} filtered rows
        </button>
      </div>

      <div className="panel-card">
        <div className="chart-card-header">
          <h3 style={{ marginRight: "auto" }}>Dashboard</h3>
          <button className="btn-secondary chart-card-add" onClick={addChart}>
            <Icon name="plus" size={14} /> Add chart
          </button>
        </div>
        <div className="chart-grid">
          {charts.map((cfg) => (
            <ChartCard
              key={cfg.id}
              cfg={cfg}
              rows={filtered}
              numericOptions={numericFeaturesWithTarget}
              categoricalOptions={categoricalFeaturesWithTarget}
              allOptions={allFeaturesWithTarget}
              palette={theme.categoryPalette}
              labelFor={labelFor}
              onChange={(patch) => updateChart(cfg.id, patch)}
              onRemove={charts.length > 1 ? () => removeChart(cfg.id) : undefined}
            />
          ))}
        </div>
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
                    <td key={c}>{displayValue(c, row[c])}</td>
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

function ChartCard({
  cfg,
  rows,
  numericOptions,
  categoricalOptions,
  allOptions,
  palette,
  labelFor,
  onChange,
  onRemove,
}: {
  cfg: ChartCardConfig;
  rows: DatasetRow[];
  numericOptions: string[];
  categoricalOptions: string[];
  allOptions: string[];
  palette: string[];
  labelFor: (f: string) => string;
  onChange: (patch: Partial<ChartCardConfig>) => void;
  onRemove?: () => void;
}) {
  const showY = cfg.type !== "histogram" && cfg.type !== "heatmap";
  const yRequired = cfg.type === "line" || cfg.type === "scatter" || cfg.type === "scatter3d" || cfg.type === "box";
  const showZ = cfg.type === "scatter3d";
  const showColorBy = cfg.type !== "box";
  const showAgg = (cfg.type === "bar" || cfg.type === "pie") && Boolean(cfg.y);
  const xOptions = cfg.type === "bar" ? allOptions : cfg.type === "pie" || cfg.type === "box" ? categoricalOptions : numericOptions;
  // Scatter/scatter3d render a numeric colorBy as a continuous gradient (see
  // chartBuilder.ts's isNumericColumn branch) — other chart types still only group
  // by discrete category, so numeric columns would be meaningless there.
  const colorByOptions = cfg.type === "scatter" || cfg.type === "scatter3d" ? allOptions : categoricalOptions;

  const { data, layout } = useMemo(() => buildChart(rows, cfg, palette, labelFor), [rows, cfg, palette, labelFor]);
  const [maximized, setMaximized] = useState(false);

  const card = (
    <div className={`panel-card${maximized ? " panel-card--maximized" : ""}`} onClick={(e) => maximized && e.stopPropagation()}>
      <div className="chart-card-header">
        <label>
          Chart type
          <select value={cfg.type} onChange={(e) => onChange({ type: e.target.value as ChartCardConfig["type"] })}>
            {CHART_TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          X{cfg.type === "box" ? " (group by)" : ""}
          <select value={cfg.x} onChange={(e) => onChange({ x: e.target.value })}>
            <option value="">—</option>
            {xOptions.map((f) => (
              <option key={f} value={f}>
                {labelFor(f)}
              </option>
            ))}
          </select>
        </label>
        {showY && (
          <label>
            Y{yRequired ? "" : " (blank = count)"}
            <select value={cfg.y} onChange={(e) => onChange({ y: e.target.value })}>
              <option value="">—</option>
              {numericOptions.map((f) => (
                <option key={f} value={f}>
                  {labelFor(f)}
                </option>
              ))}
            </select>
          </label>
        )}
        {showZ && (
          <label>
            Z
            <select value={cfg.z} onChange={(e) => onChange({ z: e.target.value })}>
              <option value="">—</option>
              {numericOptions.map((f) => (
                <option key={f} value={f}>
                  {labelFor(f)}
                </option>
              ))}
            </select>
          </label>
        )}
        {showColorBy && (
          <label>
            Color by
            <select value={cfg.colorBy} onChange={(e) => onChange({ colorBy: e.target.value })}>
              <option value="">—</option>
              {colorByOptions.map((f) => (
                <option key={f} value={f}>
                  {labelFor(f)}
                </option>
              ))}
            </select>
          </label>
        )}
        {showAgg && (
          <label>
            Aggregate
            <select value={cfg.agg} onChange={(e) => onChange({ agg: e.target.value as ChartCardConfig["agg"] })}>
              {AGG_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="chart-card-header-actions">
          <button
            className="chart-card-maximize"
            onClick={() => setMaximized((m) => !m)}
            title={maximized ? "Restore" : "Maximize"}
          >
            <Icon name={maximized ? "restore" : "maximize"} size={14} />
          </button>
          {onRemove && (
            <button className="chart-card-remove" onClick={onRemove} title="Remove chart">
              <Icon name="close" size={14} />
            </button>
          )}
        </div>
      </div>
      {data.length > 0 ? (
        <PlotlyChart data={data} layout={layout} height={maximized ? "70vh" : 320} />
      ) : (
        <p className="panel-hint">Choose fields above to render this chart.</p>
      )}
    </div>
  );

  if (!maximized) return card;
  return (
    <div className="chart-card-maximize-overlay" onClick={() => setMaximized(false)}>
      {card}
    </div>
  );
}
