import { useState } from "react";
import { predict, type FeatureSpec, type PredictionResult, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";

function defaultValue(spec: FeatureSpec): number | string {
  return spec.type === "numeric" ? spec.default : spec.default;
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
      <label>
        {feature}
        <input
          type="number"
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
    <label>
      {feature}
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

/**
 * Generic tabular_ml form, driven entirely by the scenario's feature_columns/
 * feature_schema (see libs/shared/scenario_schema.py) — no scenario-specific form
 * code, so this same component renders churn, mpm, or any future tabular_ml scenario.
 */
export function TabularView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const featureColumns = scenario.feature_columns ?? [];
  const featureSchema = scenario.feature_schema ?? {};
  const [record, setRecord] = useState<Record<string, number | string>>(() => {
    const initial: Record<string, number | string> = {};
    for (const feature of featureColumns) {
      initial[feature] = defaultValue(featureSchema[feature]);
    }
    return initial;
  });
  const [result, setResult] = useState<PredictionResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  function update(feature: string, value: number | string) {
    setRecord((r) => ({ ...r, [feature]: value }));
  }

  async function runPredict() {
    setError(null);
    try {
      const response = await predict(config.predictionUrl, scenario.slug, [record], accessToken);
      setResult(response.predictions[0]);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div>
      <h2>
        {scenario.icon} {scenario.title}
      </h2>
      <p>{scenario.description}</p>
      <div className="churn-form">
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
      <button onClick={runPredict}>Run {scenario.title}</button>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="prediction-result">
          {scenario.task_type === "regression" ? (
            <p>
              <strong>Prediction:</strong> {result.prediction.toFixed(2)}
              {scenario.target_units ? ` ${scenario.target_units}` : ""}
            </p>
          ) : (
            <p>
              <strong>Probability:</strong> {(result.prediction * 100).toFixed(1)}%
            </p>
          )}
          <ul>
            {Object.entries(result.contributions).map(([feature, value]) => (
              <li key={feature}>
                {feature}: {value.toFixed(4)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <hr />
      <h3>💬 Ask about this data</h3>
      <ChatPanel
        baseUrl={config.assistantUrl}
        scenarioSlug={scenario.slug}
        sampleQuestions={scenario.sample_questions}
        accessToken={accessToken}
      />
    </div>
  );
}
