import { useMemo, useRef } from "react";
import type { ScenarioSummary } from "./apiClient";

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
    match: (s) => s.kind === "tabular_ml" || s.kind === "tabular_ml_timeseries",
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
  if (scenario.kind === "tabular_ml_timeseries") return "Time Series";
  if (scenario.kind === "tabular_ml") return scenario.task_type === "regression" ? "Regression" : "Classification";
  if (scenario.kind === "deep_learning") return scenario.deep_learning?.modality === "image" ? "Computer Vision" : "NLP";
  return null;
}

// Techniques a scenario uses — orthogonal to both `kind` (the gallery section) and
// `industry` (the Domain filter), all derived from fields the summary already carries
// so a new scenario.yaml lands in the right buckets without UI changes. A scenario can
// carry several (e.g. a tabular model with a free-text feature is NLP *and* has an
// LLM assistant tool). Order here is the dropdown's display order.
type Technique = { key: string; label: string; match: (scenario: ScenarioSummary) => boolean };

const GRAPH_EXTRAS = new Set(["network_explorer", "money_trail", "knowledge_graph"]);
const isTabular = (s: ScenarioSummary) => s.kind === "tabular_ml" || s.kind === "tabular_ml_timeseries";
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
  { key: "time_series", label: "Time series", match: (s) => s.kind === "tabular_ml_timeseries" },
  { key: "custom_tab", label: "With a showcase tab", match: (s) => !!s.ui_extras || !!s.tutorial },
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
}: {
  scenarios: ScenarioSummary[];
  onSelect: (scenario: ScenarioSummary) => void;
  filters: ScenarioFilters;
  onFiltersChange: (filters: ScenarioFilters) => void;
}) {
  const { industry, technique, query } = filters;
  const searchRef = useRef<HTMLInputElement>(null);
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
              return (
                <button key={scenario.slug} className="scenario-card" onClick={() => onSelect(scenario)}>
                  <div className="scenario-card-icon">{scenario.icon}</div>
                  <div className="scenario-card-body">
                    <div className="scenario-card-kind">{categoryFor(scenario).label}</div>
                    <h3>{scenario.title}</h3>
                    <p>{scenario.description}</p>
                  </div>
                  <div className="scenario-card-footer">
                    {DOMAIN_CHIPS[scenario.industry] && (
                      <span className="scenario-chip scenario-chip--domain">{DOMAIN_CHIPS[scenario.industry]}</span>
                    )}
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
