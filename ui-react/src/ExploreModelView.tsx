import { useState } from "react";
import {
  predict,
  datasetSample,
  datasetEvaluation,
  datasetExplainability,
  type ScenarioSummary,
  type DatasetSample,
  type DatasetEvaluation,
  type DatasetExplainability,
} from "./apiClient";
import { config, MAX_ROWS } from "./config";
import { BarList, StatTile, ScatterPlot, Histogram, CategoryBars, LineChart, CHART_COLORS } from "./charts";
import { initialRecord, FeatureInput, type Record_ } from "./predictUtils";

// Unlike dataset sample/evaluation (cheap either way), SHAP explanation cost scales
// ~linearly with row count (~50s at MAX_ROWS on churn) — 500 rows is plenty for a
// stable global-importance ranking and stays fast (a few seconds).
const SHAP_SAMPLE_SIZE = 500;

function GlobalImportanceSection({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [data, setData] = useState<DatasetExplainability | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await datasetExplainability(config.predictionUrl, scenario.slug, SHAP_SAMPLE_SIZE, accessToken));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="panel-card">
      <h3>Global feature importance (SHAP)</h3>
      <p className="panel-hint">
        Mean(|SHAP value|) over a real sample of the dataset — how much each feature matters overall, computed the
        same way as each individual prediction's explanation, not a single estimator's built-in importances.
      </p>
      {!data && (
        <button className="btn-primary" onClick={load} disabled={loading}>
          {loading ? "Computing…" : "Compute global importance"}
        </button>
      )}
      {error && <p className="error">{error}</p>}
      {data && (
        <>
          <BarList items={data.feature_importance.map((f) => ({ label: f.feature, value: f.importance }))} signed={false} valueFormatter={(v) => v.toFixed(4)} />
          <p className="panel-hint">Computed over {data.sample_size} sampled rows.</p>
        </>
      )}
    </div>
  );
}

type Sweep = {
  feature: string;
  points: { x: number; label: string; y: number; lower?: number; upper?: number }[];
  numeric: boolean;
};

function PartialDependenceSection({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
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
    <>
      <div className="panel-card">
        <h3>Partial dependence</h3>
        <p className="panel-hint">
          Sweep one feature across its real range (others held at the values below) and see how the live model's
          prediction responds — computed from real API calls, not synthetic history.
        </p>
        <div className="feature-grid">
          {featureColumns.map((f) => (
            <FeatureInput key={f} feature={f} spec={featureSchema[f]} value={record[f]} onChange={(v) => setRecord((r) => ({ ...r, [f]: v }))} />
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
            <LineChart points={sweep.points} xLabel={sweep.feature} yLabel={yLabel} color={CHART_COLORS.green} bandColor={CHART_COLORS.green} />
          ) : (
            <CategoryBars items={sweep.points.map((p) => ({ category: p.label, score: p.y }))} color={CHART_COLORS.blue} valueFormatter={(v) => v.toFixed(2)} />
          )}
        </div>
      )}
    </>
  );
}

function ModelPerformanceSection({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [sample, setSample] = useState<DatasetSample | null>(null);
  const [evaluation, setEvaluation] = useState<DatasetEvaluation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [s, e] = await Promise.all([
        datasetSample(config.predictionUrl, scenario.slug, 30, accessToken),
        datasetEvaluation(config.predictionUrl, scenario.slug, MAX_ROWS, accessToken),
      ]);
      setSample(s);
      setEvaluation(e);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  if (!evaluation) {
    return (
      <div className="panel-card panel-card--center">
        <h3>Model performance</h3>
        <p className="panel-hint">A held-out evaluation of the deployed pipeline: metrics, predicted vs. actual, residuals, per-category breakdown.</p>
        <button className="btn-primary" onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Load evaluation"}
        </button>
        {error && <p className="error">{error}</p>}
      </div>
    );
  }

  const isRegression = evaluation.task_type === "regression";
  const scatterPoints = evaluation.predictions.map((p, i) => ({
    x: evaluation.actuals[i],
    y: p,
    lower: evaluation.prediction_lower?.[i],
    upper: evaluation.prediction_upper?.[i],
  }));
  const residuals = evaluation.predictions.map((p, i) => p - evaluation.actuals[i]);

  return (
    <>
      <div className="panel-card">
        <h3>Held-out evaluation</h3>
        <p className="panel-hint">
          Scored on training's held-out split ({evaluation.n} rows{sample ? ` of ${sample.total_rows}` : ""}) — the deployed
          pipeline was then refit on the full dataset, so this is a reference evaluation, not a strict score of the exact
          deployed weights.
        </p>
        <div className="kpi-row">
          {Object.entries(evaluation.metrics).map(([k, v]) => (
            <StatTile key={k} label={k.toUpperCase()} value={v.toFixed(k === "r2" || k.includes("auc") ? 3 : 2)} />
          ))}
        </div>
      </div>
      <div className="grid-2">
        <div className="panel-card">
          <h3>{isRegression ? "Predicted vs actual" : "Predicted probability vs actual class"}</h3>
          <ScatterPlot points={scatterPoints} xLabel="Actual" yLabel="Predicted" color={CHART_COLORS.blue} />
        </div>
        <div className="panel-card">
          <h3>{isRegression ? "Residuals" : "Predicted probability distribution"}</h3>
          <Histogram values={isRegression ? residuals : evaluation.predictions} color={CHART_COLORS.purple} zeroLine={isRegression} />
        </div>
      </div>
      {evaluation.breakdown_feature && (
        <div className="panel-card">
          <h3>
            {isRegression ? "MAE" : "Accuracy"} by {evaluation.breakdown_feature}
          </h3>
          <CategoryBars items={evaluation.breakdown} color={CHART_COLORS.amber} />
        </div>
      )}
    </>
  );
}

/** Understanding the model: global explainability, live sensitivity, and held-out
 * performance — distinct from Dataset (no ML) and ML Predictions (running the model).
 */
export function ExploreModelView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  return (
    <div className="tab-panel">
      <GlobalImportanceSection scenario={scenario} accessToken={accessToken} />
      <PartialDependenceSection scenario={scenario} accessToken={accessToken} />
      <ModelPerformanceSection scenario={scenario} accessToken={accessToken} />
    </div>
  );
}
