import { useMemo } from "react";
import type { ScenarioSummary } from "./apiClient";
import { InfoButton } from "./InfoButton";
import { featureLabel } from "./predictUtils";

/**
 * Static "about this scenario" tab — the plain-language counterpart to Data & BI's
 * numbers. Renders scenario.yaml's description/credits/target/feature_schema (see
 * libs/shared/scenario_schema.py) as a readable glossary: what the model predicts,
 * where the data comes from, and what each raw column actually means — so a user can
 * understand the scenario before poking at charts or predictions.
 */
export function ScenarioView({ scenario }: { scenario: ScenarioSummary }) {
  const featureColumns = scenario.feature_columns ?? [];
  const featureSchema = scenario.feature_schema ?? {};
  const classLabels = scenario.target_value_labels ? Object.entries(scenario.target_value_labels) : [];

  const industryLabel = useMemo(() => {
    const words = scenario.industry.split("_");
    return words.map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
  }, [scenario.industry]);

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>
          {scenario.icon} {scenario.title}
        </h3>
        <p style={{ marginTop: "-0.2rem" }}>{scenario.description}</p>
        <div className="scenario-meta-row">
          <span className="scenario-meta-pill">Industry: {industryLabel}</span>
          {scenario.task_type && <span className="scenario-meta-pill">Task: {scenario.task_type}</span>}
          <span className="scenario-meta-pill">Features: {featureColumns.length}</span>
        </div>
        {scenario.credits && (
          <p className="panel-hint" style={{ marginTop: "0.6rem" }}>
            Dataset credit: {scenario.credits.source} —{" "}
            <a href={scenario.credits.url} target="_blank" rel="noreferrer">
              original source
            </a>
            {scenario.credits.note ? ` (${scenario.credits.note})` : ""}
          </p>
        )}
      </div>

      {scenario.target && (
        <div className="panel-card">
          <h3>What the model predicts</h3>
          <p style={{ marginTop: "-0.2rem" }}>
            <strong>{scenario.target_label ?? scenario.target}</strong>
            {scenario.target_units ? ` (${scenario.target_units})` : ""}
            {" "}
            <code>{scenario.target}</code>
          </p>
          {scenario.target_description && <p className="panel-hint">{scenario.target_description}</p>}
          {classLabels.length > 0 && (
            <p className="panel-hint">
              Classes: {classLabels.map(([raw, label]) => `${label} (${raw})`).join(", ")}
            </p>
          )}
        </div>
      )}

      <div className="panel-card">
        <h3>Feature glossary</h3>
        <p className="panel-hint" style={{ marginTop: "-0.2rem" }}>
          Every input the model uses — its plain-language label, the raw dataset column behind it, and (via
          the ⓘ) what it actually measures.
        </p>
        <div className="table-scroll">
          <table className="data-table" style={{ marginTop: "0.5rem" }}>
            <thead>
              <tr>
                <th>Label</th>
                <th>Column</th>
                <th>Explanation</th>
              </tr>
            </thead>
            <tbody>
              {featureColumns.map((f) => {
                const spec = featureSchema[f];
                return (
                  <tr key={f}>
                    <td>{featureLabel(scenario, f)}</td>
                    <td>
                      <code>{f}</code>
                    </td>
                    <td>{spec?.info ? <InfoButton text={spec.info} /> : <span className="panel-hint">—</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {scenario.sample_questions.length > 0 && (
        <div className="panel-card">
          <h3>Things you can ask the assistant</h3>
          <ul>
            {scenario.sample_questions.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
