import { useState } from "react";
import {
  predict,
  datasetSample,
  datasetEvaluation,
  type FeatureSpec,
  type ScenarioSummary,
  type DatasetSample,
  type DatasetEvaluation,
} from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";
import { BarList, StatTile, Gauge, ScatterPlot, Histogram, CategoryBars, LineChart, CHART_COLORS } from "./charts";

type Tab = "predict" | "explore" | "dataset";

type Record_ = Record<string, number | string>;

function defaultValue(spec: FeatureSpec): number | string {
  return spec.default;
}

function initialRecord(featureColumns: string[], featureSchema: Record<string, FeatureSpec>): Record_ {
  const initial: Record_ = {};
  for (const feature of featureColumns) initial[feature] = defaultValue(featureSchema[feature]);
  return initial;
}

/** Map transformed (one-hot) SHAP contribution keys back to the original feature the
 * user actually sees in the form — numeric features match directly; for a categorical
 * feature only the one-hot column matching the record's *selected* value is kept
 * (the unselected columns' contributions aren't meaningful to show per-feature).
 */
function mapContributions(record: Record_, contributions: Record<string, number>): { label: string; value: number }[] {
  const items: { label: string; value: number }[] = [];
  for (const [name, value] of Object.entries(contributions)) {
    const unprefixed = name.includes("__") ? name.slice(name.indexOf("__") + 2) : name;
    if (unprefixed in record) {
      items.push({ label: unprefixed, value });
      continue;
    }
    const match = Object.entries(record).find(([f, v]) => unprefixed === `${f}_${v}`);
    if (match) items.push({ label: match[0], value });
  }
  return items;
}

function FeatureInput({
  feature,
  spec,
  value,
  onChange,
}: {
  feature: string;
  spec: FeatureSpec;
  value: number | string;
  onChange: (value: number | string) => void;
}) {
  if (spec.type === "numeric") {
    return (
      <label className="feature-input">
        <span className="feature-input-label">
          {feature} <span className="feature-input-range">{spec.min}–{spec.max}</span>
        </span>
        <input
          type="range"
          min={spec.min}
          max={spec.max}
          step={spec.step ?? 1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        <input
          type="number"
          className="feature-input-number"
          min={spec.min}
          max={spec.max}
          step={spec.step ?? 1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </label>
    );
  }
  return (
    <label className="feature-input">
      <span className="feature-input-label">{feature}</span>
      <select value={value as string} onChange={(e) => onChange(e.target.value)}>
        {spec.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

function exportJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function PredictTab({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const featureColumns = scenario.feature_columns ?? [];
  const featureSchema = scenario.feature_schema ?? {};
  const [record, setRecord] = useState<Record_>(() => initialRecord(featureColumns, featureSchema));
  const [result, setResult] = useState<{
    prediction: number;
    contributions: Record<string, number>;
    prediction_lower: number | null;
    prediction_upper: number | null;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function update(feature: string, value: number | string) {
    setRecord((r) => ({ ...r, [feature]: value }));
    setResult(null);
  }

  async function runPredict() {
    setError(null);
    setLoading(true);
    try {
      const response = await predict(config.predictionUrl, scenario.slug, [record], accessToken);
      setResult(response.predictions[0]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  const isRegression = scenario.task_type === "regression";
  const contributionItems = result ? mapContributions(record, result.contributions) : [];

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>Input</h3>
        <div className="feature-grid">
          {featureColumns.map((feature) => (
            <FeatureInput
              key={feature}
              feature={feature}
              spec={featureSchema[feature]}
              value={record[feature]}
              onChange={(value) => update(feature, value)}
            />
          ))}
        </div>
        <button className="btn-primary" onClick={runPredict} disabled={loading}>
          {loading ? "Running…" : `Run ${scenario.title}`}
        </button>
        {error && <p className="error">{error}</p>}
      </div>

      {result && (
        <div className="panel-card">
          <h3>Prediction</h3>
          <div className="predict-result-row">
            {isRegression ? (
              <StatTile
                label="Prediction"
                value={`${result.prediction.toFixed(2)} ${scenario.target_units ?? ""}`}
                sub={
                  result.prediction_lower !== null && result.prediction_upper !== null
                    ? `90% interval: ${result.prediction_lower.toFixed(1)} – ${result.prediction_upper.toFixed(1)} ${scenario.target_units ?? ""}`
                    : undefined
                }
                color={CHART_COLORS.green}
              />
            ) : (
              <Gauge value={result.prediction} color={result.prediction >= 0.5 ? CHART_COLORS.red : CHART_COLORS.green} label="probability" />
            )}
            <button
              className="btn-secondary"
              onClick={() => exportJson(`${scenario.slug}-prediction.json`, { input: record, result })}
            >
              ⬇ Export result
            </button>
          </div>
          <h4>Explained prediction (SHAP)</h4>
          <BarList items={contributionItems} valueFormatter={(v) => v.toFixed(4)} />
        </div>
      )}

      <div className="panel-card panel-card--chat">
        <ChatPanel
          baseUrl={config.assistantUrl}
          scenarioSlug={scenario.slug}
          sampleQuestions={scenario.sample_questions}
          accessToken={accessToken}
          title="💬 Ask the assistant about this data"
        />
      </div>
    </div>
  );
}

type Sweep = {
  feature: string;
  points: { x: number; label: string; y: number; lower?: number; upper?: number }[];
  numeric: boolean;
};

function ExploreTab({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const featureColumns = scenario.feature_columns ?? [];
  const featureSchema = scenario.feature_schema ?? {};
  const [feature, setFeature] = useState(featureColumns[0] ?? "");
  const [record, setRecord] = useState<Record_>(() => initialRecord(featureColumns, featureSchema));
  const [sweep, setSweep] = useState<Sweep | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runSweep() {
    const spec = featureSchema[feature];
    setLoading(true);
    setError(null);
    try {
      let xValues: (number | string)[];
      if (spec.type === "numeric") {
        const steps = 14;
        xValues = Array.from({ length: steps }, (_, i) => Math.round((spec.min + (i / (steps - 1)) * (spec.max - spec.min)) * 100) / 100);
      } else {
        xValues = spec.options;
      }
      const records = xValues.map((v) => ({ ...record, [feature]: v }));
      const response = await predict(config.predictionUrl, scenario.slug, records, accessToken);
      const points = response.predictions.map((p, i) => ({
        x: spec.type === "numeric" ? (xValues[i] as number) : i,
        label: String(xValues[i]),
        y: p.prediction,
        lower: p.prediction_lower ?? undefined,
        upper: p.prediction_upper ?? undefined,
      }));
      setSweep({ feature, points, numeric: spec.type === "numeric" });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  const yLabel = scenario.task_type === "regression" ? `Prediction (${scenario.target_units ?? ""})` : "Probability";

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>Explore model behavior</h3>
        <p className="panel-hint">
          Sweep one feature across its real range (others held at the values below) and see how the live model's
          prediction responds — a partial-dependence view computed from real API calls, not synthetic history.
        </p>
        <div className="feature-grid">
          {featureColumns.map((f) => (
            <FeatureInput
              key={f}
              feature={f}
              spec={featureSchema[f]}
              value={record[f]}
              onChange={(v) => setRecord((r) => ({ ...r, [f]: v }))}
            />
          ))}
        </div>
        <div className="explore-controls">
          <label>
            Sweep feature
            <select value={feature} onChange={(e) => setFeature(e.target.value)}>
              {featureColumns.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <button className="btn-primary" onClick={runSweep} disabled={loading}>
            {loading ? "Running…" : "Run sweep"}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </div>

      {sweep && (
        <div className="panel-card">
          <h3>
            {yLabel} vs {sweep.feature}
          </h3>
          {sweep.numeric ? (
            <LineChart
              points={sweep.points}
              xLabel={sweep.feature}
              yLabel={yLabel}
              color={CHART_COLORS.green}
              bandColor={CHART_COLORS.green}
            />
          ) : (
            <CategoryBars
              items={sweep.points.map((p) => ({ category: p.label, score: p.y }))}
              color={CHART_COLORS.blue}
              valueFormatter={(v) => v.toFixed(2)}
            />
          )}
        </div>
      )}
    </div>
  );
}

function DatasetTab({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [sample, setSample] = useState<DatasetSample | null>(null);
  const [evaluation, setEvaluation] = useState<DatasetEvaluation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [s, e] = await Promise.all([
        datasetSample(config.predictionUrl, scenario.slug, 30, accessToken),
        datasetEvaluation(config.predictionUrl, scenario.slug, 400, accessToken),
      ]);
      setSample(s);
      setEvaluation(e);
      setLoaded(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  if (!loaded) {
    return (
      <div className="tab-panel">
        <div className="panel-card panel-card--center">
          <p className="panel-hint">
            Load a real sample of this scenario's dataset and a held-out evaluation of the deployed model
            (metrics, feature importance, predicted vs. actual).
          </p>
          <button className="btn-primary" onClick={load} disabled={loading}>
            {loading ? "Loading…" : `Load ${scenario.title} dataset`}
          </button>
          {error && <p className="error">{error}</p>}
        </div>
      </div>
    );
  }

  const isRegression = evaluation!.task_type === "regression";
  const metricEntries = Object.entries(evaluation!.metrics);
  const scatterPoints = evaluation!.predictions.map((p, i) => ({
    x: evaluation!.actuals[i],
    y: p,
    lower: evaluation!.prediction_lower?.[i],
    upper: evaluation!.prediction_upper?.[i],
  }));
  const residuals = evaluation!.predictions.map((p, i) => p - evaluation!.actuals[i]);

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>Held-out evaluation</h3>
        <p className="panel-hint">
          Scored on training's held-out split ({evaluation!.n} rows) — the deployed pipeline was then refit on the
          full dataset, so this is a reference evaluation, not a strict score of the exact deployed weights.
        </p>
        <div className="kpi-row">
          {metricEntries.map(([k, v]) => (
            <StatTile key={k} label={k.toUpperCase()} value={v.toFixed(k === "r2" || k.includes("auc") ? 3 : 2)} />
          ))}
          <StatTile label="Dataset rows" value={String(sample!.total_rows)} />
        </div>
      </div>

      <div className="panel-card">
        <h3>Feature importance</h3>
        <BarList
          items={evaluation!.feature_importance.map((f) => ({ label: f.feature, value: f.importance }))}
          signed={false}
          valueFormatter={(v) => v.toFixed(3)}
        />
      </div>

      <div className="grid-2">
        <div className="panel-card">
          <h3>{isRegression ? "Predicted vs actual" : "Predicted probability vs actual class"}</h3>
          <ScatterPlot points={scatterPoints} xLabel="Actual" yLabel="Predicted" color={CHART_COLORS.blue} />
        </div>
        <div className="panel-card">
          <h3>{isRegression ? "Residuals" : "Predicted probability distribution"}</h3>
          <Histogram values={isRegression ? residuals : evaluation!.predictions} color={CHART_COLORS.purple} zeroLine={isRegression} />
        </div>
      </div>

      {evaluation!.breakdown_feature && (
        <div className="panel-card">
          <h3>
            {isRegression ? "MAE" : "Accuracy"} by {evaluation!.breakdown_feature}
          </h3>
          <CategoryBars items={evaluation!.breakdown} color={CHART_COLORS.amber} />
        </div>
      )}

      <div className="panel-card">
        <h3>Sample rows</h3>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                {sample!.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sample!.rows.map((row, i) => (
                <tr key={i}>
                  {sample!.columns.map((c) => (
                    <td key={c}>{String(row[c])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <button
          className="btn-secondary"
          onClick={() =>
            exportJson(`${scenario.slug}-sample.json`, sample)
          }
        >
          ⬇ Export sample
        </button>
      </div>
    </div>
  );
}

/**
 * Generic tabular_ml workspace, driven entirely by the scenario's feature_columns/
 * feature_schema (see libs/shared/scenario_schema.py) plus prediction's /predict and
 * /dataset endpoints — no scenario-specific code, so this same component renders
 * churn, mpm, supply_chain, or any future tabular_ml scenario.
 */
export function TabularView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [tab, setTab] = useState<Tab>("predict");

  return (
    <div className="workspace">
      <div className="workspace-tabs">
        <button className={tab === "predict" ? "active" : ""} onClick={() => setTab("predict")}>
          🎯 Predict &amp; Explain
        </button>
        <button className={tab === "explore" ? "active" : ""} onClick={() => setTab("explore")}>
          🔍 Explore model
        </button>
        <button className={tab === "dataset" ? "active" : ""} onClick={() => setTab("dataset")}>
          📊 Dataset &amp; performance
        </button>
      </div>
      {tab === "predict" && <PredictTab scenario={scenario} accessToken={accessToken} />}
      {tab === "explore" && <ExploreTab scenario={scenario} accessToken={accessToken} />}
      {tab === "dataset" && <DatasetTab scenario={scenario} accessToken={accessToken} />}
    </div>
  );
}
