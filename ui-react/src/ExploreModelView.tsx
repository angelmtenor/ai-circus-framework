import { useEffect, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import {
  predict,
  datasetSample,
  datasetEvaluation,
  datasetExplainability,
  modelDrift,
  type ScenarioSummary,
  type DatasetSample,
  type DatasetEvaluation,
  type DatasetExplainability,
  type ModelDrift,
} from "./apiClient";
import { config, MAX_ROWS } from "./config";
import { BarList, StatTile, ScatterPlot, Histogram, CategoryBars, LineChart, CHART_COLORS } from "./charts";
import { initialRecord, featureLabel, FeatureInput, type Record_ } from "./predictUtils";
import { InfoButton } from "./InfoButton";
import { METRIC_INFO, PRIMARY_METRIC } from "./metricGlossary";

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

  useCopilotReadable({
    description: `Global SHAP feature importance for the ${scenario.title} model, if the user has computed it — which features matter most overall, distinct from any single prediction's explanation.`,
    value: data ? { feature_importance: data.feature_importance, sample_size: data.sample_size } : null,
  });

  return (
    <div className="panel-card">
      <h3>
        What matters most <InfoButton text="Mean(|SHAP value|) over a real sample of the dataset — computed the same way as each individual prediction's explanation, not a single estimator's built-in importances." />
      </h3>
      <p className="panel-hint">Which inputs move the model's predictions the most, overall.</p>
      {!data && (
        <button className="btn-primary" onClick={load} disabled={loading}>
          {loading ? "Computing…" : "Compute global importance"}
        </button>
      )}
      {error && <p className="error">{error}</p>}
      {data && (
        <>
          <BarList
            items={data.feature_importance.map((f) => ({ label: featureLabel(scenario, f.feature), value: f.importance }))}
            signed={false}
            valueFormatter={(v) => v.toFixed(4)}
          />
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
  // Free text can't be swept across a range — it stays an editable input only.
  const sweepable = featureColumns.filter((f) => featureSchema[f]?.type !== "text");
  const [feature, setFeature] = useState(sweepable[0] ?? "");
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
      } else if (spec.type === "categorical") {
        xValues = spec.options;
      } else {
        return;
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

  useCopilotReadable({
    description: `The current partial-dependence sweep for ${scenario.title}, if the user has run one — how the model's prediction responds as "${feature}" varies, other features held at the values shown.`,
    value: sweep ? { held_at: record, ...sweep } : null,
  });

  return (
    <>
      <div className="panel-card">
        <h3>
          Try different values <InfoButton text="Sweeps one feature across its real range (others held at the values below) and shows how the live model's prediction responds — computed from real API calls, not synthetic history." />
        </h3>
        <p className="panel-hint">See how changing one input, on its own, shifts the model's prediction.</p>
        <div className="feature-grid">
          {featureColumns.map((f) => (
            <FeatureInput key={f} feature={f} spec={featureSchema[f]} value={record[f]} onChange={(v) => setRecord((r) => ({ ...r, [f]: v }))} />
          ))}
        </div>
        <div className="explore-controls">
          <label>
            Sweep feature
            <select value={feature} onChange={(e) => setFeature(e.target.value)}>
              {sweepable.map((f) => (
                <option key={f} value={f}>
                  {featureLabel(scenario, f)}
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
            {yLabel} vs {featureLabel(scenario, sweep.feature)}
          </h3>
          {sweep.numeric ? (
            <LineChart
              points={sweep.points}
              xLabel={featureLabel(scenario, sweep.feature)}
              yLabel={yLabel}
              color={CHART_COLORS.green}
              bandColor={CHART_COLORS.green}
            />
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

  useCopilotReadable({
    description: `The held-out evaluation metrics for the ${scenario.title} model currently shown to the user, if loaded.`,
    value: evaluation ? { metrics: evaluation.metrics, n: evaluation.n, task_type: evaluation.task_type } : null,
  });

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
        <h3>
          Held-out evaluation{" "}
          <InfoButton text={`Scored on training's held-out split (${evaluation.n} rows${sample ? ` of ${sample.total_rows}` : ""}) — the deployed pipeline was then refit on the full dataset, so this is a reference evaluation, not a strict score of the exact deployed weights.`} />
        </h3>
        <p className="panel-hint">How well the model performs on real data it wasn't trained on.</p>
        <div className="kpi-row">
          {Object.entries(evaluation.metrics).map(([k, v]) => (
            <StatTile
              key={k}
              label={METRIC_INFO[k]?.label ?? k.toUpperCase()}
              value={k === "mape" ? `${v.toFixed(2)}%` : v.toFixed(k === "r2" || k.includes("auc") ? 3 : 2)}
              info={METRIC_INFO[k]?.info}
              highlight={k === PRIMARY_METRIC[evaluation.task_type]}
            />
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
            {isRegression ? METRIC_INFO.mae.label : METRIC_INFO.accuracy.label} by {featureLabel(scenario, evaluation.breakdown_feature)}
          </h3>
          <CategoryBars items={evaluation.breakdown} color={CHART_COLORS.amber} />
        </div>
      )}
    </>
  );
}

type DriftLevel = "stable" | "moderate" | "significant";

const DRIFT_LEVELS: Record<DriftLevel, { label: string; color: string; hint: string }> = {
  stable: { label: "Stable", color: CHART_COLORS.green, hint: "Recent traffic looks like the data the model was trained on." },
  moderate: {
    label: "Moderate shift",
    color: CHART_COLORS.amber,
    hint: "Recent traffic has started to differ from the training data — worth watching.",
  },
  significant: {
    label: "Significant shift",
    color: CHART_COLORS.red,
    hint: "Recent traffic no longer looks like the training data — predictions may be less reliable; consider retraining.",
  },
};

function driftLevel(psi: number, thresholds: ModelDrift["thresholds"]): DriftLevel {
  return psi > thresholds.significant ? "significant" : psi > thresholds.moderate ? "moderate" : "stable";
}

const PSI_INFO =
  "Population Stability Index: how far the distribution of what this model scored lately has moved from its training data. " +
  "Below 0.1 is stable, 0.1–0.25 a moderate shift, above 0.25 a significant one. Computed over your organisation's own " +
  "recent requests (up to 2,000 rows, then a fresh window), per input and for the model's own output.";

/** Whether recent scoring traffic still looks like the training data (prediction's GET /model/{slug}/drift). */
function DriftSection({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [drift, setDrift] = useState<ModelDrift | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshes, setRefreshes] = useState(0);

  useEffect(() => {
    let cancelled = false;
    modelDrift(config.predictionUrl, scenario.slug, accessToken)
      .then((d) => {
        if (!cancelled) {
          setDrift(d);
          setError(null);
        }
      })
      .catch((e) => !cancelled && setError((e as Error).message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [scenario.slug, accessToken, refreshes]);

  const load = () => {
    setLoading(true);
    setRefreshes((n) => n + 1);
  };

  const enoughRows = !!drift && drift.window_rows >= drift.min_rows;
  const worst = drift && enoughRows ? Math.max(drift.score_psi ?? 0, ...drift.features.map((f) => f.psi)) : null;
  const level = drift && worst !== null ? driftLevel(worst, drift.thresholds) : null;

  useCopilotReadable({
    description: `Data drift of the ${scenario.title} model: PSI of the user's organisation's recent scoring traffic vs. the training data (higher = more shifted).`,
    value: drift && enoughRows ? { level, score_psi: drift.score_psi, features: drift.features.slice(0, 5), rows: drift.window_rows } : null,
  });

  return (
    <div className="panel-card">
      <h3>
        Data drift <InfoButton text={PSI_INFO} />
        <button className="drift-refresh" onClick={load} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </h3>
      {error && <p className="error">{error}</p>}
      {drift && !drift.available && (
        <p className="panel-hint">
          This model was trained before drift tracking existed. Retraining it (<code>make k3s-pipeline</code>) starts comparing
          live traffic with its training data.
        </p>
      )}
      {drift?.available && !enoughRows && (
        <p className="panel-hint">
          Collecting traffic: {drift.window_rows} of the {drift.min_rows} scored rows needed before drift is meaningful. Score
          some records in ML Predictions or the Data tab.
        </p>
      )}
      {drift && enoughRows && level && (
        <>
          <p className={`drift-status drift-status--${level}`}>
            {DRIFT_LEVELS[level].label}
            <span>{DRIFT_LEVELS[level].hint}</span>
          </p>
          <div className="kpi-row">
            <StatTile
              label="Output shift (PSI)"
              value={drift.score_psi !== null ? drift.score_psi.toFixed(3) : "—"}
              color={drift.score_psi !== null ? DRIFT_LEVELS[driftLevel(drift.score_psi, drift.thresholds)].color : undefined}
              info="How far the distribution of the model's own scores has moved."
            />
            {drift.features[0] && (
              <StatTile
                label="Most shifted input"
                value={featureLabel(scenario, drift.features[0].feature)}
                sub={`PSI ${drift.features[0].psi.toFixed(3)}`}
                color={DRIFT_LEVELS[driftLevel(drift.features[0].psi, drift.thresholds)].color}
              />
            )}
            <StatTile label="Rows compared" value={drift.window_rows.toLocaleString()} sub={`window of up to ${drift.window_size.toLocaleString()}`} />
          </div>
          <BarList
            items={drift.features.map((f) => ({ label: featureLabel(scenario, f.feature), value: f.psi }))}
            signed={false}
            neutralColor={DRIFT_LEVELS[level].color}
            valueFormatter={(v) => v.toFixed(3)}
          />
        </>
      )}
    </div>
  );
}

/** Understanding the model: global explainability, live sensitivity, and held-out
 * performance — distinct from Dataset (no ML) and ML Predictions (running the model).
 */
export function ExploreModelView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  return (
    <div className="tab-panel">
      <DriftSection scenario={scenario} accessToken={accessToken} />
      <ModelPerformanceSection scenario={scenario} accessToken={accessToken} />
      <GlobalImportanceSection scenario={scenario} accessToken={accessToken} />
      <PartialDependenceSection scenario={scenario} accessToken={accessToken} />
    </div>
  );
}
