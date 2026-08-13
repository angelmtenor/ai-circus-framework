import type { ScenarioSummary } from "./apiClient";

type Category = {
  key: string;
  label: string;
  match: (scenario: ScenarioSummary) => boolean;
};

const CATEGORIES: Category[] = [
  {
    key: "machine_learning",
    label: "Machine Learning",
    match: (s) => s.kind === "tabular_ml" || s.kind === "tabular_ml_timeseries",
  },
  {
    key: "conversational_assistant",
    label: "Conversational Assistant",
    match: (s) => s.kind === "conversational_rag",
  },
  {
    key: "assisted_form",
    label: "Assisted Forms",
    match: (s) => s.kind === "assisted_form",
  },
  {
    key: "specific_agents",
    label: "Specific Agents",
    match: (s) => s.kind === "agent",
  },
];

function categoryFor(scenario: ScenarioSummary): Category {
  return (
    CATEGORIES.find((category) => category.match(scenario)) ?? {
      key: scenario.kind,
      label: scenario.kind,
      match: () => false,
    }
  );
}

function mlSubtype(scenario: ScenarioSummary): string | null {
  if (scenario.kind === "tabular_ml_timeseries") return "Time Series";
  if (scenario.kind === "tabular_ml") return scenario.task_type === "regression" ? "Regression" : "Classification";
  return null;
}

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

  const groups = CATEGORIES.map((category) => ({
    ...category,
    scenarios: scenarios.filter(category.match),
  })).filter((group) => group.scenarios.length > 0);

  return (
    <div className="scenario-groups">
      {groups.map((group) => (
        <section key={group.key} className="scenario-group">
          <h2 className="scenario-group-title">{group.label}</h2>
          <div className="scenario-grid">
            {group.scenarios.map((scenario) => {
              const subtype = mlSubtype(scenario);
              return (
                <button key={scenario.slug} className="scenario-card" onClick={() => onSelect(scenario)}>
                  <div className="scenario-card-icon">{scenario.icon}</div>
                  <div className="scenario-card-body">
                    <div className="scenario-card-kind">{categoryFor(scenario).label}</div>
                    <h3>{scenario.title}</h3>
                    <p>{scenario.description}</p>
                  </div>
                  <div className="scenario-card-footer">
                    {subtype && <span className="scenario-chip">{subtype}</span>}
                    <span className="scenario-card-open">Open →</span>
                  </div>
                </button>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
