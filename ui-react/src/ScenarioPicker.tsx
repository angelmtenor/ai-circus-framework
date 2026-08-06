import type { ScenarioSummary } from "./apiClient";

const KIND_LABEL: Record<string, string> = {
  tabular_ml: "Tabular ML",
  conversational_rag: "Document Q&A",
};

export function ScenarioPicker({
  scenarios,
  onSelect,
}: {
  scenarios: ScenarioSummary[];
  onSelect: (scenario: ScenarioSummary) => void;
}) {
  if (scenarios.length === 0) {
    return (
      <div className="scenario-empty">
        <span className="scenario-empty-icon">🗂️</span>
        <p>No scenarios are assigned to your account yet. Contact your admin.</p>
      </div>
    );
  }

  return (
    <div className="scenario-grid">
      {scenarios.map((scenario) => (
        <button key={scenario.slug} className="scenario-card" onClick={() => onSelect(scenario)}>
          <div className="scenario-card-icon">{scenario.icon}</div>
          <div className="scenario-card-body">
            <div className="scenario-card-kind">{KIND_LABEL[scenario.kind] ?? scenario.kind}</div>
            <h3>{scenario.title}</h3>
            <p>{scenario.description}</p>
          </div>
          <div className="scenario-card-footer">
            {scenario.kind === "tabular_ml" && (
              <span className="scenario-chip">{scenario.task_type === "regression" ? "Regression" : "Classification"}</span>
            )}
            <span className="scenario-card-open">Open →</span>
          </div>
        </button>
      ))}
    </div>
  );
}
