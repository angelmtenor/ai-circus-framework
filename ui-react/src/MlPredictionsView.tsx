import { useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { predict, datasetSample, type ScenarioSummary, type DatasetSample } from "./apiClient";
import { config, MAX_ROWS } from "./config";
import { BarList, StatTile, Gauge, CHART_COLORS } from "./charts";
import { DatasetFilterPanel, type DatasetRow } from "./DatasetFilterPanel";
import { mapContributions, topContribution, exportJson, initialRecord, featureLabel, FeatureInput, type Record_ } from "./predictUtils";

// A batch /predict call computes a per-row SHAP explanation for every record in one
// batched call — the same ~linear-in-row-count cost as explainability's "compute
// global importance" (see ExploreModelView.tsx's SHAP_SAMPLE_SIZE), so this stays
// well under MAX_ROWS (which is fine for the cheap, model-free dataset fetch below).
const BATCH_PREDICT_CAP = 500;

function IndividualMode({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
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
  const contributionItems = result
    ? mapContributions(record, result.contributions).map((item) => ({ ...item, label: featureLabel(scenario, item.label) }))
    : [];

  // Live "what's on screen" context for the chat agent — the *current* prediction,
  // not the scenario's static grounding (see assistant's build_system_prompt). Only
  // populated once a prediction has actually been run, so "explain this prediction"
  // has real values to reference; null beforehand is itself meaningful (nothing run yet).
  useCopilotReadable({
    description: `The individual ${scenario.title} prediction currently shown to the user: input feature values, the model's predicted value, and each feature's SHAP contribution. Use this if asked to explain "this" or "the current" prediction.`,
    value: result ? { input: record, ...result } : null,
  });

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>Input</h3>
        <div className="feature-grid">
          {featureColumns.map((feature) => (
            <FeatureInput key={feature} feature={feature} spec={featureSchema[feature]} value={record[feature]} onChange={(v) => update(feature, v)} />
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
            <button className="btn-secondary" onClick={() => exportJson(`${scenario.slug}-prediction.json`, { input: record, result })}>
              ⬇ Export result
            </button>
          </div>
          <h4>Explained prediction (SHAP)</h4>
          <BarList items={contributionItems} valueFormatter={(v) => v.toFixed(4)} />
        </div>
      )}
    </div>
  );
}

type RankedRow = {
  record: Record_;
  prediction: number;
  prediction_lower: number | null;
  prediction_upper: number | null;
  top: { label: string; value: number } | null;
};

function BatchMode({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const featureColumns = scenario.feature_columns ?? [];
  const featureSchema = scenario.feature_schema ?? {};
  const [sample, setSample] = useState<DatasetSample | null>(null);
  const [filtered, setFiltered] = useState<DatasetRow[]>([]);
  const [ranked, setRanked] = useState<RankedRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Capped to the top 20 rows — enough for the agent to describe trends/outliers
  // without ballooning every chat request with a potentially 500-row batch result.
  // Called unconditionally (before the early return below) — React Hooks rule.
  useCopilotReadable({
    description: `Ranked batch predictions for ${scenario.title} currently shown to the user (top rows by predicted value, out of ${ranked?.length ?? 0} total). Use this if asked about "these results" or trends across the current batch.`,
    value: ranked ? ranked.slice(0, 20).map((r) => ({ ...r.record, prediction: r.prediction, top_feature: r.top?.label })) : null,
  });

  async function load() {
    setError(null);
    try {
      setSample(await datasetSample(config.predictionUrl, scenario.slug, MAX_ROWS, accessToken));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function runBatch() {
    setLoading(true);
    setError(null);
    try {
      const records = filtered.slice(0, BATCH_PREDICT_CAP).map((row) => {
        const record: Record_ = {};
        for (const f of featureColumns) record[f] = row[f] as number | string;
        return record;
      });
      const response = await predict(config.predictionUrl, scenario.slug, records, accessToken);
      const rows: RankedRow[] = response.predictions.map((p, i) => ({
        record: records[i],
        prediction: p.prediction,
        prediction_lower: p.prediction_lower,
        prediction_upper: p.prediction_upper,
        top: topContribution(records[i], p.contributions),
      }));
      rows.sort((a, b) => b.prediction - a.prediction);
      setRanked(rows);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  if (!sample) {
    return (
      <div className="tab-panel">
        <div className="panel-card panel-card--center">
          <p className="panel-hint">Query a subset of the real dataset, then run this scenario's model on every matching row — ranked, explained, exportable.</p>
          <button className="btn-primary" onClick={load}>
            Load {scenario.title} dataset
          </button>
          {error && <p className="error">{error}</p>}
        </div>
      </div>
    );
  }

  const isRegression = scenario.task_type === "regression";

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>Query</h3>
        <DatasetFilterPanel featureColumns={featureColumns} featureSchema={featureSchema} rows={sample.rows} onFilteredChange={setFiltered} />
        <button className="btn-primary" onClick={runBatch} disabled={loading || filtered.length === 0} style={{ marginTop: "0.6rem" }}>
          {loading ? "Running…" : `Predict on ${Math.min(filtered.length, BATCH_PREDICT_CAP)} rows`}
        </button>
        {filtered.length > BATCH_PREDICT_CAP && <span className="panel-hint"> (capped at {BATCH_PREDICT_CAP})</span>}
        {error && <p className="error">{error}</p>}
      </div>

      {ranked && (
        <div className="panel-card">
          <h3>Ranked predictions ({ranked.length})</h3>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  {featureColumns.map((f) => (
                    <th key={f}>{featureLabel(scenario, f)}</th>
                  ))}
                  <th>Prediction</th>
                  {isRegression && <th>90% interval</th>}
                  <th>Top feature</th>
                </tr>
              </thead>
              <tbody>
                {ranked.map((row, i) => (
                  <tr key={i}>
                    <td>{i + 1}</td>
                    {featureColumns.map((f) => (
                      <td key={f}>{String(row.record[f])}</td>
                    ))}
                    <td>{isRegression ? `${row.prediction.toFixed(2)} ${scenario.target_units ?? ""}` : `${(row.prediction * 100).toFixed(1)}%`}</td>
                    {isRegression && (
                      <td>{row.prediction_lower !== null ? `${row.prediction_lower.toFixed(1)} – ${row.prediction_upper?.toFixed(1)}` : "—"}</td>
                    )}
                    <td>{row.top ? `${featureLabel(scenario, row.top.label)} (${row.top.value.toFixed(3)})` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="btn-secondary" onClick={() => exportJson(`${scenario.slug}-ranked-predictions.json`, ranked)} style={{ marginTop: "0.6rem" }}>
            ⬇ Export ranked predictions
          </button>
        </div>
      )}
    </div>
  );
}

/** Running the model — one record at a time, or batch over a real, queried subset of
 * the dataset (mirrors mlops_templates' Ranked/Individual prediction modes).
 */
export function MlPredictionsView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [subMode, setSubMode] = useState<"individual" | "batch">("individual");
  return (
    <div>
      <div className="sub-tabs">
        <button className={subMode === "individual" ? "active" : ""} onClick={() => setSubMode("individual")}>
          Individual
        </button>
        <button className={subMode === "batch" ? "active" : ""} onClick={() => setSubMode("batch")}>
          Batch / query
        </button>
      </div>
      {subMode === "individual" ? (
        <IndividualMode scenario={scenario} accessToken={accessToken} />
      ) : (
        <BatchMode scenario={scenario} accessToken={accessToken} />
      )}
    </div>
  );
}
