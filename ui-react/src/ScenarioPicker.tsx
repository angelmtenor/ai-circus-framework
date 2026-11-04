import { useMemo, useRef, useState } from "react";
import { setComponentRunning, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import {
  missingServices,
  refreshServiceAvailability,
  scenarioServices,
  SERVICES,
  useServiceAvailability,
  useUntrainedScenarios,
} from "./serviceAvailability";

type Category = {
  key: string;
  label: string;
  match: (scenario: ScenarioSummary) => boolean;
};

// Mirrors scenario_schema.Industry (libs/shared) — the scenario's *domain*: mostly
// industries, plus learning/society domains that aren't an industry at all. Order here
// is the dropdown's display order, not just a lookup table. (The wire field is still
// called `industry`; see scenario_schema.py for why it wasn't renamed.)
export const INDUSTRY_LABELS: Record<string, string> = {
  banking_finance: "Banking & Finance",
  manufacturing_industry: "Manufacturing & Industry",
  energy_utilities: "Energy & Utilities",
  retail: "Retail",
  logistics: "Logistics",
  public_sector: "Public Sector",
  healthcare: "Healthcare",
  general: "General",
  tutorial: "Tutorials",
  society_ethics: "Society & Ethics",
};

// Domains that aren't industries — grouped separately in the Domain dropdown and
// flagged with a chip on their scenario cards.
const LEARNING_DOMAINS = new Set(["tutorial", "society_ethics"]);
const DOMAIN_CHIPS: Record<string, string> = { tutorial: "Tutorial", society_ethics: "Society & Ethics" };

export function domainLabel(domain: string): string {
  return INDUSTRY_LABELS[domain] ?? domain.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

const CATEGORIES: Category[] = [
  {
    key: "machine_learning",
    label: "Machine Learning",
    match: (s) => s.kind === "tabular_ml",
  },
  {
    key: "deep_learning",
    label: "Deep Learning",
    match: (s) => s.kind === "deep_learning",
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
    key: "data_platform",
    label: "Data Platform",
    match: (s) => s.kind === "data_product",
  },
  {
    key: "specific_agents",
    label: "Specific Agents",
    match: (s) => s.kind === "agent",
  },
];

// Card order inside each kind: alphabetical by slug (the registry's list has no
// guaranteed order), with the public-sector scenarios grouped at the end.
const LAST_DOMAINS = new Set(["public_sector"]);

function galleryOrder(a: ScenarioSummary, b: ScenarioSummary): number {
  const last = Number(LAST_DOMAINS.has(a.industry)) - Number(LAST_DOMAINS.has(b.industry));
  return last || a.slug.localeCompare(b.slug);
}

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
  if (scenario.kind === "tabular_ml") {
    if (scenario.task_type === "forecast") return "Time Series";
    return scenario.task_type === "regression" ? "Regression" : "Classification";
  }
  if (scenario.kind === "deep_learning") return scenario.deep_learning?.modality === "image" ? "Computer Vision" : "NLP";
  if (scenario.kind === "data_product") return "Lakehouse · marts · governance";
  return null;
}

// Techniques a scenario uses — orthogonal to both `kind` (the gallery section) and
// `industry` (the Domain filter), all derived from fields the summary already carries
// so a new scenario.yaml lands in the right buckets without UI changes. A scenario can
// carry several (e.g. a tabular model with a free-text feature is NLP *and* has an
// LLM assistant tool). Order here is the dropdown's display order.
type Technique = { key: string; label: string; match: (scenario: ScenarioSummary) => boolean };

const GRAPH_EXTRAS = new Set(["network_explorer", "money_trail", "knowledge_graph"]);
const isTabular = (s: ScenarioSummary) => s.kind === "tabular_ml";
const hasTextFeature = (s: ScenarioSummary) =>
  Object.values(s.feature_schema ?? {}).some((spec) => spec.type === "text");

const TECHNIQUES: Technique[] = [
  { key: "tabular_only", label: "Tabular ML only", match: (s) => isTabular(s) && !hasTextFeature(s) },
  {
    key: "nlp",
    label: "NLP / text",
    match: (s) => hasTextFeature(s) || (s.kind === "deep_learning" && s.deep_learning?.modality === "text"),
  },
  {
    key: "vision",
    label: "Computer vision",
    match: (s) => s.kind === "deep_learning" && s.deep_learning?.modality === "image",
  },
  {
    key: "llm",
    label: "LLM & RAG",
    match: (s) =>
      s.kind === "conversational_rag" || s.kind === "assisted_form" || !!s.document_tool || !!s.rubric_check,
  },
  { key: "graph", label: "Graphs & networks", match: (s) => GRAPH_EXTRAS.has(s.ui_extras?.kind ?? "") },
  { key: "data_platform", label: "Data engineering", match: (s) => s.kind === "data_product" },
  { key: "time_series", label: "Time series", match: (s) => s.task_type === "forecast" },
  { key: "custom_tab", label: "With a showcase tab", match: (s) => !!s.ui_extras || !!s.tutorial || s.kind === "data_product" },
];

// Case- and accent-insensitive ("prestacion" finds "prestación").
function normalize(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

function searchText(scenario: ScenarioSummary): string {
  return normalize(
    [
      scenario.title,
      scenario.description,
      scenario.slug.replaceAll("_", " "),
      domainLabel(scenario.industry),
      categoryFor(scenario).label,
      mlSubtype(scenario) ?? "",
      ...TECHNIQUES.filter((t) => t.match(scenario)).map((t) => t.label),
    ].join(" "),
  );
}

// The picker's filters — owned by App.tsx (not local state) so they survive this
// component unmounting when the user opens a scenario and comes back.
export type ScenarioFilters = { industry: string; technique: string; query: string };
export const DEFAULT_SCENARIO_FILTERS: ScenarioFilters = { industry: "all", technique: "all", query: "" };

export function ScenarioPicker({
  scenarios,
  onSelect,
  filters,
  onFiltersChange,
  isAdmin = false,
  accessToken = null,
}: {
  scenarios: ScenarioSummary[];
  onSelect: (scenario: ScenarioSummary) => void;
  filters: ScenarioFilters;
  onFiltersChange: (filters: ScenarioFilters) => void;
  isAdmin?: boolean;
  accessToken?: string | null;
}) {
  const { industry, technique, query } = filters;
  const searchRef = useRef<HTMLInputElement>(null);
  const neededServices = useMemo(
    () => scenarios.flatMap((s) => [...scenarioServices(s).required, ...scenarioServices(s).optional]),
    [scenarios],
  );
  const availability = useServiceAvailability(neededServices);
  const [starting, setStarting] = useState<Record<string, string>>({}); // control name -> status line
  const tabularSlugs = useMemo(() => scenarios.filter((s) => s.kind === "tabular_ml").map((s) => s.slug), [scenarios]);
  const untrained = useUntrainedScenarios(tabularSlugs, availability.get("prediction") === "up", accessToken);

  const startService = (control: string) => {
    setStarting((s) => ({ ...s, [control]: "Starting…" }));
    setComponentRunning(config.dataPlatformManagerUrl, control, true, accessToken)
      .then(() => {
        setStarting((s) => ({ ...s, [control]: "Starting — this can take a minute…" }));
        // The pod needs a while to load its models; re-probe a few times rather than wait 30 s.
        for (const delay of [10_000, 25_000, 45_000]) window.setTimeout(refreshServiceAvailability, delay);
      })
      .catch((e) => setStarting((s) => ({ ...s, [control]: `Could not start: ${(e as Error).message}` })));
  };
  const availableIndustries = useMemo(
    () => Object.keys(INDUSTRY_LABELS).filter((key) => scenarios.some((s) => s.industry === key)),
    [scenarios],
  );
  // Only techniques at least one entitled scenario uses, with how many do.
  const availableTechniques = useMemo(
    () =>
      TECHNIQUES.map((t) => ({ ...t, count: scenarios.filter(t.match).length })).filter((t) => t.count > 0),
    [scenarios],
  );
  const haystacks = useMemo(() => new Map(scenarios.map((s) => [s.slug, searchText(s)])), [scenarios]);

  if (scenarios.length === 0) {
    return (
      <div className="scenario-empty">
        <span className="scenario-empty-icon">🗂️</span>
        <p>No scenarios are assigned to your account yet. Contact your admin.</p>
      </div>
    );
  }

  const setFilter = (patch: Partial<ScenarioFilters>) => onFiltersChange({ ...filters, ...patch });
  const activeTechnique = TECHNIQUES.find((t) => t.key === technique);
  // Every word must appear somewhere (title, description, domain, technique…).
  const words = normalize(query).split(/\s+/).filter(Boolean);
  const filteredScenarios = scenarios.filter(
    (s) =>
      (industry === "all" || s.industry === industry) &&
      (!activeTechnique || activeTechnique.match(s)) &&
      words.every((word) => haystacks.get(s.slug)?.includes(word)),
  );
  const filtering = industry !== "all" || !!activeTechnique || words.length > 0;

  const groups = CATEGORIES.map((category) => ({
    ...category,
    scenarios: filteredScenarios.filter(category.match).sort(galleryOrder),
  })).filter((group) => group.scenarios.length > 0);

  return (
    <div className="scenario-groups">
      <div className="scenario-toolbar">
        <div className="scenario-search">
          <span className="scenario-search-icon" aria-hidden="true">
            🔍
          </span>
          <input
            ref={searchRef}
            type="search"
            placeholder="Find a scenario…"
            aria-label="Find a scenario"
            value={query}
            onChange={(e) => setFilter({ query: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Escape") setFilter({ query: "" });
            }}
          />
        </div>
        {availableIndustries.length > 1 && (
          <div className="scenario-industry-filter">
            <label htmlFor="scenario-industry-select">Domain</label>
            <select
              id="scenario-industry-select"
              value={industry}
              onChange={(e) => setFilter({ industry: e.target.value })}
            >
              <option value="all">All domains</option>
              {[
                { label: "Industries", keys: availableIndustries.filter((key) => !LEARNING_DOMAINS.has(key)) },
                { label: "Learning & society", keys: availableIndustries.filter((key) => LEARNING_DOMAINS.has(key)) },
              ]
                .filter((group) => group.keys.length > 0)
                .map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {group.keys.map((key) => (
                      <option key={key} value={key}>
                        {INDUSTRY_LABELS[key]}
                      </option>
                    ))}
                  </optgroup>
                ))}
            </select>
          </div>
        )}
        {availableTechniques.length > 1 && (
          <div className="scenario-industry-filter">
            <label htmlFor="scenario-technique-select">Technique</label>
            <select
              id="scenario-technique-select"
              value={technique}
              onChange={(e) => setFilter({ technique: e.target.value })}
            >
              <option value="all">All techniques</option>
              {availableTechniques.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label} ({t.count})
                </option>
              ))}
            </select>
          </div>
        )}
        {filtering && (
          <div className="scenario-filter-status">
            <span>
              {filteredScenarios.length} of {scenarios.length}
            </span>
            <button
              type="button"
              className="scenario-filter-clear"
              onClick={() => {
                onFiltersChange(DEFAULT_SCENARIO_FILTERS);
                searchRef.current?.focus();
              }}
            >
              Clear
            </button>
          </div>
        )}
      </div>
      {groups.length === 0 && (
        <div className="scenario-empty">
          <span className="scenario-empty-icon">🗂️</span>
          <p>No scenarios match these filters.</p>
        </div>
      )}
      {groups.map((group) => (
        <section key={group.key} className="scenario-group">
          <h2 className="scenario-group-title">{group.label}</h2>
          <div className="scenario-grid">
            {group.scenarios.map((scenario) => {
              const subtype = mlSubtype(scenario);
              const missing = missingServices(scenario, availability);
              const degraded = scenarioServices(scenario).optional.filter((key) => availability.get(key) === "down");
              const body = (
                <>
                  <div className="scenario-card-icon">{scenario.icon}</div>
                  <div className="scenario-card-body">
                    <div className="scenario-card-kind">{categoryFor(scenario).label}</div>
                    <h3>{scenario.title}</h3>
                    <p>{scenario.description}</p>
                  </div>
                </>
              );
              const chips = (
                <>
                  {DOMAIN_CHIPS[scenario.industry] && (
                    <span className="scenario-chip scenario-chip--domain">{DOMAIN_CHIPS[scenario.industry]}</span>
                  )}
                  {subtype && <span className="scenario-chip">{subtype}</span>}
                </>
              );
              if (missing.length === 0 && untrained.has(scenario.slug)) {
                return (
                  <div key={scenario.slug} className="scenario-card scenario-card--unavailable">
                    {body}
                    <div className="scenario-card-unavailable" role="status">
                      <span title="Run: make k3s-pipeline (compose: make pipeline)">
                        {"⏸ Model not trained (run `make k3s-pipeline`)"}
                      </span>
                    </div>
                    <div className="scenario-card-footer">
                      {chips}
                      <span className="scenario-card-open">Unavailable</span>
                    </div>
                  </div>
                );
              }
              if (missing.length > 0) {
                // A div, not a button: it cannot be opened, and may hold the admin's Start button
                // (so no aria-disabled here — it would disable that button for assistive tech too).
                const controls = [...new Set(missing.map((key) => SERVICES[key].control).filter((c): c is string => !!c))];
                return (
                  <div key={scenario.slug} className="scenario-card scenario-card--unavailable">
                    {body}
                    <div className="scenario-card-unavailable" role="status">
                      {missing.map((key) => (
                        <span key={key} title={`To start it: ${SERVICES[key].startHint}`}>
                          ⏸ {SERVICES[key].label} is not started
                        </span>
                      ))}
                      {isAdmin &&
                        controls.map((control) =>
                          starting[control] ? (
                            <span key={control} className="scenario-card-starting">
                              {starting[control]}
                            </span>
                          ) : (
                            <button key={control} type="button" className="scenario-card-start" onClick={() => startService(control)}>
                              Start {control}
                            </button>
                          ),
                        )}
                    </div>
                    <div className="scenario-card-footer">
                      {chips}
                      <span className="scenario-card-open">Unavailable</span>
                    </div>
                  </div>
                );
              }
              return (
                <button key={scenario.slug} className="scenario-card" onClick={() => onSelect(scenario)}>
                  {body}
                  <div className="scenario-card-footer">
                    {chips}
                    {degraded.length > 0 && (
                      <span
                        className="scenario-chip scenario-chip--warn"
                        title={degraded.map((key) => `${SERVICES[key].label} is not started — to start it: ${SERVICES[key].startHint}`).join("\n")}
                      >
                        Chat unavailable
                      </span>
                    )}
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
