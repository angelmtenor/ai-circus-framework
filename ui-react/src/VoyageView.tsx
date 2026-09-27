import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { datasetSample, modelCard, predict, type ExampleRecord, type ScenarioSummary, type VoyageExplorerExtra } from "./apiClient";
import { config } from "./config";
import { StatTile } from "./charts";
import { explainByFeature, featureLabel, FeatureInput, initialRecord, type Record_ } from "./predictUtils";
import { packZones, SCENE_H, SCENE_W, SceneBackdrop, SceneForeground, type Point } from "./voyageScene";
import "./voyage.css";

// Diverging scale for P(positive class): red pole -> neutral gray -> blue pole.
// Validated (dataviz validate_palette.js, dark, scene surface #0b1626): lightness band,
// CVD ΔE 22 (protan), normal-vision ΔE 31, contrast >= 3:1.
const POSITIVE = [63, 147, 235]; // #3f93eb
const NEGATIVE = [234, 79, 88]; // #ea4f58
const MIDPOINT = [125, 130, 140]; // #7d828c
const POSITIVE_HEX = "#3f93eb";
const NEGATIVE_HEX = "#ea4f58";
const CORRECT_HEX = "#3a4150"; // "model was right" recedes in the surprises view
const PROBE_RADIUS = 11; // hover/click target (scene units) — bigger than any dot
const SAMPLE_LIMIT = 5000;

type Mode = "model" | "fate" | "surprises";

type Person = {
  index: number;
  id: string;
  name: string | null;
  record: Record_;
  actual: 0 | 1;
  zone: number;
  prediction: number;
  base: number;
  explanation: { feature: string; value: number }[];
  at: Point;
};

type WhatIf = { source: string; original: Person | null; record: Record_ };

type Loaded = {
  people: Person[];
  radius: number;
  spare: Point[];
  truncated: { shown: number; total: number } | null;
  auc: number | null;
};

// Session cache (module scope): one scored, berthed passenger list per scenario+caller.
const VOYAGE_CACHE = new Map<string, Promise<Loaded>>();

async function loadVoyage(
  scenario: ScenarioSummary,
  extras: VoyageExplorerExtra,
  featureColumns: string[],
  zoneIndex: Map<string, number>,
  target: string,
  accessToken: string | null,
): Promise<Loaded> {
  const cardPromise = modelCard(config.predictionUrl, scenario.slug, accessToken).catch(() => null);
  const sample = await datasetSample(config.predictionUrl, scenario.slug, SAMPLE_LIMIT, accessToken);
  const rows = sample.rows.filter((row) => zoneIndex.has(String(row[extras.zone_by])));
  const records = rows.map((row) => Object.fromEntries(featureColumns.map((f) => [f, row[f] as number | string])));
  const response = await predict(config.predictionUrl, scenario.slug, records, accessToken);
  const people = rows.map((row, index) => {
    const prediction = response.predictions[index].prediction;
    const { base, items } = explainByFeature(featureColumns, prediction, response.predictions[index].contributions);
    return {
      index,
      id: sample.id_column ? String(row[sample.id_column]) : String(index + 1),
      name: extras.name_column ? String(row[extras.name_column] ?? "") : null,
      record: records[index],
      actual: Number(row[target]) === 1 ? 1 : 0,
      zone: zoneIndex.get(String(row[extras.zone_by]))!,
      prediction,
      base,
      explanation: items,
      at: { x: 0, y: 0 },
    } satisfies Person;
  });
  // Berth: within each zone, most-likely survivors first — the zone reads as a gradient.
  const byZone = extras.zones.map((_, z) => people.filter((p) => p.zone === z).sort((a, b) => b.prediction - a.prediction));
  const packed = packZones(byZone.map((z) => z.length));
  byZone.forEach((zonePeople, z) => zonePeople.forEach((p, i) => (p.at = packed.slots[z][i])));
  const card = await cardPromise;
  return {
    people,
    radius: packed.radius,
    spare: byZone.map((zonePeople, z) => packed.slots[z][zonePeople.length]),
    truncated: sample.total_rows > sample.rows.length ? { shown: sample.rows.length, total: sample.total_rows } : null,
    auc: card ? (card.metrics.cv_roc_auc_mean ?? card.metrics.holdout_roc_auc ?? null) : null,
  };
}

function mix(a: number[], b: number[], t: number): string {
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

function probabilityColor(p: number): string {
  return p < 0.5 ? mix(NEGATIVE, MIDPOINT, p / 0.5) : mix(MIDPOINT, POSITIVE, (p - 0.5) / 0.5);
}

function pct(p: number, digits = 0): string {
  return `${(p * 100).toFixed(digits)}%`;
}

function points(v: number): string {
  const pts = v * 100;
  return `${pts >= 0 ? "+" : "−"}${Math.abs(pts).toFixed(1)} pts`;
}

/**
 * Generic 5th workspace tab for a binary-classification tabular_ml scenario that sets
 * `ui_extras: {kind: voyage_explorer, ...}` (see libs/shared/scenario_schema.py) —
 * `titanic` is the first user. Every real row is drawn as one person aboard the
 * `ocean_liner` scene (voyageScene.tsx), berthed by `zone_by`, scored in one batched
 * /predict call (probability + SHAP) and coloured by it; the scenario's own wording
 * fields are the only domain vocabulary here.
 */
export function VoyageView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as VoyageExplorerExtra;
  const featureColumns = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const featureSchema = useMemo(() => scenario.feature_schema ?? {}, [scenario.feature_schema]);
  const target = scenario.target ?? "";
  const positiveLabel = scenario.target_value_labels?.["1"] ?? "Positive";
  const negativeLabel = scenario.target_value_labels?.["0"] ?? "Negative";
  const noun = extras.person_noun;
  const zoneIndex = useMemo(() => new Map(extras.zones.map((z, i) => [z.key, i])), [extras.zones]);
  const categoricalFilters = useMemo(() => [extras.zone_by, ...extras.filters.filter((f) => f !== extras.zone_by)], [extras]);

  const [people, setPeople] = useState<Person[] | null>(null);
  const [radius, setRadius] = useState(3);
  const [spare, setSpare] = useState<Point[]>([]);
  const [truncated, setTruncated] = useState<{ shown: number; total: number } | null>(null);
  const [auc, setAuc] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("model");
  const [selected, setSelected] = useState<Person | null>(null);
  const [hovered, setHovered] = useState<Person | null>(null);
  const [whatIf, setWhatIf] = useState<WhatIf | null>(null);
  const [chips, setChips] = useState<Record<string, Set<string>>>({});
  const [ranges, setRanges] = useState<Record<string, [number, number]>>({});
  const [search, setSearch] = useState("");

  // Load every row, score it once (probability + SHAP), berth it — cached per scenario
  // and caller for the session, so leaving and re-opening the tab is instant.
  useEffect(() => {
    let cancelled = false;
    setError(null);
    const key = `${scenario.slug}|${accessToken ?? ""}`;
    let pending = VOYAGE_CACHE.get(key);
    if (!pending) {
      pending = loadVoyage(scenario, extras, featureColumns, zoneIndex, target, accessToken);
      VOYAGE_CACHE.set(key, pending);
    }
    pending
      .then((loaded) => {
        if (cancelled) return;
        setRadius(loaded.radius);
        setSpare(loaded.spare);
        setTruncated(loaded.truncated);
        setAuc(loaded.auc);
        setPeople(loaded.people);
      })
      .catch((e) => {
        VOYAGE_CACHE.delete(key);
        if (!cancelled) setError((e as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, [scenario, accessToken, featureColumns, extras, zoneIndex, target]);

  const matches = useMemo(() => {
    if (!people) return new Set<number>();
    const needle = search.trim().toLowerCase();
    return new Set(
      people
        .filter(
          (p) =>
            Object.entries(chips).every(([f, allowed]) => allowed.size === 0 || allowed.has(String(p.record[f]))) &&
            Object.entries(ranges).every(([f, [lo, hi]]) => Number(p.record[f]) >= lo && Number(p.record[f]) <= hi) &&
            (!needle || p.id.toLowerCase().includes(needle) || (p.name ?? "").toLowerCase().includes(needle)),
        )
        .map((p) => p.index),
    );
  }, [people, chips, ranges, search]);
  const filtering = Object.values(chips).some((s) => s.size > 0) || Object.keys(ranges).length > 0 || search.trim() !== "";

  const cohort = useMemo(() => {
    const members = (people ?? []).filter((p) => matches.has(p.index));
    const n = members.length;
    const actualRate = n ? members.reduce((s, p) => s + p.actual, 0) / n : 0;
    const predictedRate = n ? members.reduce((s, p) => s + p.prediction, 0) / n : 0;
    const surprises = members.filter((p) => (p.prediction >= 0.5 ? 1 : 0) !== p.actual).length;
    return { n, actualRate, predictedRate, surprises };
  }, [people, matches]);

  const zoneStats = useMemo(
    () =>
      extras.zones.map((_, z) => {
        const members = (people ?? []).filter((p) => p.zone === z);
        const n = members.length || 1;
        return {
          n: members.length,
          actual: members.reduce((s, p) => s + p.actual, 0) / n,
          predicted: members.reduce((s, p) => s + p.prediction, 0) / n,
        };
      }),
    [people, extras.zones],
  );

  // Live "what's on screen" context for the assistant dock.
  useCopilotReadable({
    description: `The ${extras.tab_label} view of ${scenario.title}: the ${noun} currently selected (features, model probability of "${positiveLabel}", real outcome, SHAP explanation in probability points) and the filtered cohort's real vs predicted "${positiveLabel}" rate.`,
    value: {
      selected: selected
        ? { id: selected.id, name: selected.name, features: selected.record, probability: selected.prediction, actual: selected.actual ? positiveLabel : negativeLabel, explanation: selected.explanation.slice(0, 6) }
        : null,
      cohort: { filters: Object.fromEntries(Object.entries(chips).map(([f, s]) => [f, [...s]])), ranges, ...cohort },
    },
  });

  const probe = useRef<SVGSVGElement>(null);
  function personAt(event: MouseEvent<SVGSVGElement>): Person | null {
    const svg = probe.current;
    if (!svg || !people) return null;
    const box = svg.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * SCENE_W;
    const y = ((event.clientY - box.top) / box.height) * SCENE_H;
    let best: Person | null = null;
    let bestDistance = PROBE_RADIUS;
    for (const p of people) {
      const d = Math.hypot(p.at.x - x, p.at.y - y);
      if (d < bestDistance) {
        best = p;
        bestDistance = d;
      }
    }
    return best;
  }

  function toggleChip(feature: string, option: string) {
    setChips((prev) => {
      const next = new Set(prev[feature] ?? []);
      if (next.has(option)) next.delete(option);
      else next.add(option);
      return { ...prev, [feature]: next };
    });
  }

  function select(person: Person | null) {
    setSelected(person);
    setWhatIf(null);
  }

  if (error) {
    return (
      <div className="tab-panel">
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!people) {
    return (
      <div className="tab-panel">
        <div className="voy-loading">
          <span className="voy-loading-ship">🚢</span>
          Boarding every {noun} and asking the model about each one…
        </div>
      </div>
    );
  }

  const suggestions =
    search.trim().length >= 2
      ? people.filter((p) => matches.has(p.index)).slice(0, 6)
      : [];
  const whatIfZone = whatIf ? zoneIndex.get(String(whatIf.record[extras.zone_by])) : undefined;

  return (
    <div className="tab-panel voy">
      <div className="panel-card voy-header">
        <div>
          <h3>{extras.title}</h3>
          {extras.subtitle && <p className="panel-hint">{extras.subtitle}</p>}
        </div>
        <div className="kpi-row">
          <StatTile label={`${noun[0].toUpperCase()}${noun.slice(1)}s aboard`} value={people.length.toLocaleString()} />
          <StatTile label={`Really ${positiveLabel.toLowerCase()}`} value={pct(people.reduce((s, p) => s + p.actual, 0) / people.length)} />
          <StatTile label="Model predicts" value={pct(people.reduce((s, p) => s + p.prediction, 0) / people.length)} sub="average probability" />
          {auc !== null && <StatTile label="Model ROC AUC" value={auc.toFixed(3)} sub="cross-validated" />}
        </div>
      </div>

      <div className="voy-toolbar">
        <div className="sub-tabs voy-modes" role="tablist">
          <button className={mode === "model" ? "active" : ""} onClick={() => setMode("model")}>
            Model's prediction
          </button>
          <button className={mode === "fate" ? "active" : ""} onClick={() => setMode("fate")}>
            Reveal real fate
          </button>
          <button className={mode === "surprises" ? "active" : ""} onClick={() => setMode("surprises")}>
            Model vs reality
          </button>
        </div>
        <div className="voy-search">
          <input
            type="search"
            placeholder={`Find a ${noun} by name or id…`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label={`Find a ${noun}`}
          />
          {suggestions.length > 0 && (
            <ul className="voy-suggestions">
              {suggestions.map((p) => (
                <li key={p.index}>
                  <button onClick={() => select(p)}>
                    <span>{p.name || `#${p.id}`}</span>
                    <span className="panel-hint">#{p.id}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="voy-stage">
        <svg
          ref={probe}
          className={`voy-scene voy-scene--${mode}${filtering ? " voy-scene--filtering" : ""}`}
          viewBox={`0 0 ${SCENE_W} ${SCENE_H}`}
          role="img"
          aria-label={`${people.length} ${noun}s aboard, coloured by ${mode === "model" ? "predicted probability" : "real outcome"}`}
          onMouseMove={(e) => setHovered(personAt(e))}
          onMouseLeave={() => setHovered(null)}
          onClick={(e) => {
            const person = personAt(e);
            if (person) select(person);
          }}
        >
          <SceneBackdrop />
          <g className="voy-people">
            {people.map((p) => {
              const dim = filtering && !matches.has(p.index);
              const wrong = (p.prediction >= 0.5 ? 1 : 0) !== p.actual;
              let fill = probabilityColor(p.prediction);
              let stroke = "none";
              if (mode === "fate") {
                // Secondary encoding: the negative outcome is also a hollow ring, never colour alone.
                fill = p.actual ? POSITIVE_HEX : "transparent";
                stroke = p.actual ? "none" : NEGATIVE_HEX;
              } else if (mode === "surprises") {
                fill = wrong ? (p.actual ? POSITIVE_HEX : NEGATIVE_HEX) : CORRECT_HEX;
              }
              return (
                <circle
                  key={p.index}
                  cx={p.at.x}
                  cy={p.at.y}
                  r={radius}
                  fill={fill}
                  stroke={stroke}
                  strokeWidth={stroke === "none" ? 0 : radius * 0.5}
                  className={`voy-person${dim ? " voy-person--dim" : ""}${mode === "surprises" && wrong && !dim ? " voy-person--glow" : ""}`}
                  // The reveal sweeps from bow to stern, the way the ship went down.
                  style={{ transitionDelay: `${((SCENE_W - p.at.x) / SCENE_W) * 0.9}s` }}
                />
              );
            })}
          </g>
          {selected && (
            <g className="voy-selected" pointerEvents="none">
              <circle cx={selected.at.x} cy={selected.at.y} r={radius * 3.2} />
              <line x1={selected.at.x} x2={selected.at.x} y1={0} y2={selected.at.y - radius * 3.2} />
            </g>
          )}
          {whatIf && whatIfZone !== undefined && spare[whatIfZone] && (
            <g className="voy-you" pointerEvents="none" transform={`translate(${spare[whatIfZone].x} ${spare[whatIfZone].y})`}>
              <circle r={radius * 3} />
              <path d="M0 -6 L1.8 -1.8 L6 -1.5 L2.8 1.4 L3.7 5.8 L0 3.4 L-3.7 5.8 L-2.8 1.4 L-6 -1.5 L-1.8 -1.8 Z" />
            </g>
          )}
          <SceneForeground nameplate={extras.title.split("·")[0].trim().toUpperCase()} />
        </svg>
        {hovered && (
          <div
            className="voy-tooltip"
            style={{ left: `${(hovered.at.x / SCENE_W) * 100}%`, top: `${(hovered.at.y / SCENE_H) * 100}%` }}
          >
            <strong>{hovered.name || `#${hovered.id}`}</strong>
            <span>{[extras.zones[hovered.zone].label, ...extras.filters.map((f) => String(hovered.record[f]))].join(" · ")}</span>
            <span>
              Model: {pct(hovered.prediction)} {positiveLabel.toLowerCase()}
              {mode !== "model" && ` · really ${(hovered.actual ? positiveLabel : negativeLabel).toLowerCase()}`}
            </span>
          </div>
        )}
      </div>

      <div className="voy-legend">
        {mode === "model" ? (
          <>
            <span>0%</span>
            <span className="voy-legend-ramp" />
            <span>100% probability of “{positiveLabel.toLowerCase()}”</span>
          </>
        ) : mode === "fate" ? (
          <>
            <span className="voy-legend-dot voy-legend-dot--pos" /> {positiveLabel}
            <span className="voy-legend-dot voy-legend-dot--neg" /> {negativeLabel} (hollow)
          </>
        ) : (
          <>
            <span className="voy-legend-dot voy-legend-dot--pos" /> {positiveLabel} although the model said no
            <span className="voy-legend-dot voy-legend-dot--neg voy-legend-dot--solid" /> {negativeLabel} although the model said yes
            <span className="voy-legend-dot voy-legend-dot--ok" /> model was right
          </>
        )}
        {truncated && (
          <span className="panel-hint">
            (showing {truncated.shown.toLocaleString()} of {truncated.total.toLocaleString()})
          </span>
        )}
      </div>

      <div className="voy-zones">
        {extras.zones.map((zone, z) => (
          <button
            key={zone.key}
            className={`voy-zone${chips[extras.zone_by]?.has(zone.key) ? " active" : ""}`}
            onClick={() => toggleChip(extras.zone_by, zone.key)}
            title={zone.description ?? undefined}
          >
            <strong>{zone.label}</strong>
            <span>
              {zoneStats[z].n} {noun}s · model {pct(zoneStats[z].predicted)}
              {mode !== "model" && ` · real ${pct(zoneStats[z].actual)}`}
            </span>
            {zone.description && <small>{zone.description}</small>}
          </button>
        ))}
      </div>

      <div className="voy-panels">
        <div className="panel-card">
          <h3>Select a cohort</h3>
          {categoricalFilters.map((feature) => {
            const spec = featureSchema[feature];
            if (!spec || spec.type !== "categorical") return null;
            return (
              <div className="filter-row" key={feature}>
                <span className="filter-row-label">{featureLabel(scenario, feature)}</span>
                <div className="filter-row-chips">
                  {spec.options.map((option) => (
                    <button key={option} className={`filter-chip${chips[feature]?.has(option) ? " active" : ""}`} onClick={() => toggleChip(feature, option)}>
                      {option}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
          {extras.range_filters.map((feature) => {
            const spec = featureSchema[feature];
            if (!spec || spec.type !== "numeric") return null;
            const [lo, hi] = ranges[feature] ?? [spec.min, spec.max];
            const set = (next: [number, number]) =>
              setRanges((prev) => {
                if (next[0] <= spec.min && next[1] >= spec.max) {
                  const rest = { ...prev };
                  delete rest[feature];
                  return rest;
                }
                return { ...prev, [feature]: next };
              });
            return (
              <div className="filter-row" key={feature}>
                <span className="filter-row-label">{featureLabel(scenario, feature)}</span>
                <div className="voy-range">
                  <input type="range" min={spec.min} max={spec.max} step={spec.step ?? 1} value={lo} onChange={(e) => set([Math.min(Number(e.target.value), hi), hi])} aria-label={`${feature} minimum`} />
                  <input type="range" min={spec.min} max={spec.max} step={spec.step ?? 1} value={hi} onChange={(e) => set([lo, Math.max(Number(e.target.value), lo)])} aria-label={`${feature} maximum`} />
                  <span>
                    {lo} – {hi}
                  </span>
                </div>
              </div>
            );
          })}
          {filtering && (
            <button
              className="btn-secondary"
              onClick={() => {
                setChips({});
                setRanges({});
                setSearch("");
              }}
            >
              Clear filters
            </button>
          )}
          <CohortSummary cohort={cohort} noun={noun} positiveLabel={positiveLabel} filtering={filtering} revealed={mode !== "model"} />
        </div>

        <div className="panel-card">
          {whatIf ? (
            <WhatIfPanel
              scenario={scenario}
              accessToken={accessToken}
              whatIf={whatIf}
              onChange={setWhatIf}
              onClose={() => setWhatIf(null)}
              positiveLabel={positiveLabel}
            />
          ) : selected ? (
            <>
              <div className="voy-person-head">
                <div>
                  <h3>{selected.name || `${noun} #${selected.id}`}</h3>
                  <p className="panel-hint">
                    #{selected.id} · {extras.zones[selected.zone].label}
                  </p>
                </div>
                <span className={`voy-fate voy-fate--${selected.actual ? "pos" : "neg"}`}>
                  Really {(selected.actual ? positiveLabel : negativeLabel).toLowerCase()}
                </span>
              </div>
              <Waterfall
                scenario={scenario}
                record={selected.record}
                base={selected.base}
                prediction={selected.prediction}
                items={selected.explanation}
                positiveLabel={positiveLabel}
                noun={noun}
              />
              <button
                className="btn-primary"
                onClick={() => setWhatIf({ source: selected.name || `#${selected.id}`, original: selected, record: { ...selected.record } })}
              >
                What if…? Edit this {noun}
              </button>
            </>
          ) : (
            <>
              <h3>Pick a {noun}</h3>
              <p className="panel-hint">
                Click anyone aboard (or search by name) to see the model's probability and exactly which facts about them pushed it up or
                down. Or put someone new aboard:
              </p>
              <PersonaPicker
                personas={extras.personas}
                onPick={(persona) => setWhatIf({ source: persona.label, original: null, record: { ...persona.record } })}
                onBlank={() => setWhatIf({ source: `a new ${noun}`, original: null, record: initialRecord(featureColumns, featureSchema) })}
                noun={noun}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function CohortSummary({
  cohort,
  noun,
  positiveLabel,
  filtering,
  revealed,
}: {
  cohort: { n: number; actualRate: number; predictedRate: number; surprises: number };
  noun: string;
  positiveLabel: string;
  filtering: boolean;
  revealed: boolean;
}) {
  const label = positiveLabel.toLowerCase();
  return (
    <div className="voy-cohort">
      <h4>{filtering ? `This cohort: ${cohort.n} ${noun}s` : `Everyone aboard: ${cohort.n} ${noun}s`}</h4>
      {cohort.n === 0 ? (
        <p className="panel-hint">Nobody matches — loosen a filter.</p>
      ) : (
        <>
          <Meter label="Model expects" value={cohort.predictedRate} className="voy-meter--model" />
          <Meter label={`Really ${label}`} value={revealed ? cohort.actualRate : null} className="voy-meter--real" />
          <p className="panel-hint">
            {revealed
              ? `Average model probability ${pct(cohort.predictedRate)}; real rate ${pct(cohort.actualRate)}. At the 50% line the model got ${cohort.n - cohort.surprises} of ${cohort.n} right and misjudged ${cohort.surprises}.`
              : `Switch to “Reveal real fate” to compare with what really happened (“${label}”).`}
          </p>
        </>
      )}
    </div>
  );
}

function Meter({ label, value, className }: { label: string; value: number | null; className: string }) {
  return (
    <div className={`voy-meter ${className}`}>
      <span>{label}</span>
      <div className="voy-meter-track">{value !== null && <div className="voy-meter-fill" style={{ width: pct(value, 1) }} />}</div>
      <strong>{value === null ? "?" : pct(value)}</strong>
    </div>
  );
}

function PersonaPicker({
  personas,
  onPick,
  onBlank,
  noun,
}: {
  personas: ExampleRecord[];
  onPick: (persona: ExampleRecord) => void;
  onBlank: () => void;
  noun: string;
}) {
  return (
    <div className="voy-personas">
      {personas.map((persona) => (
        <button key={persona.label} className="voy-persona" onClick={() => onPick(persona)}>
          <strong>{persona.label}</strong>
          {persona.description && <span>{persona.description}</span>}
        </button>
      ))}
      <button className="voy-persona voy-persona--blank" onClick={onBlank}>
        <strong>+ Build your own {noun}</strong>
        <span>Start from the default values and set every detail yourself.</span>
      </button>
    </div>
  );
}

/** A SHAP waterfall in probability points: the average output, then each feature's
 * push up or down, ending exactly at this record's probability. */
function Waterfall({
  scenario,
  record,
  base,
  prediction,
  items,
  positiveLabel,
  noun,
}: {
  scenario: ScenarioSummary;
  record: Record_;
  base: number;
  prediction: number;
  items: { feature: string; value: number }[];
  positiveLabel: string;
  noun: string;
}) {
  const shown = items.slice(0, 6);
  const rest = items.slice(6).reduce((s, i) => s + i.value, 0);
  const rows = [...shown.map((i) => ({ label: `${featureLabel(scenario, i.feature)} = ${record[i.feature]}`, value: i.value })), ...(items.length > 6 ? [{ label: "All other features", value: rest }] : [])];
  let running = base;
  const ups = shown.filter((i) => i.value > 0.005).slice(0, 2);
  const downs = shown.filter((i) => i.value < -0.005).slice(0, 2);
  const phrase = (list: typeof shown) => list.map((i) => `${featureLabel(scenario, i.feature).toLowerCase()} = ${record[i.feature]} (${points(i.value)})`).join(" and ");
  return (
    <div className="voy-explain">
      <div className="voy-explain-headline">
        <span className="voy-explain-prob" style={{ color: probabilityColor(prediction) }}>
          {pct(prediction)}
        </span>
        <span>
          probability of “{positiveLabel.toLowerCase()}”
          <br />
          <small className="panel-hint">
            {ups.length > 0 && `Raised mostly by ${phrase(ups)}`}
            {ups.length > 0 && downs.length > 0 && "; "}
            {downs.length > 0 && `lowered by ${phrase(downs)}`}.
          </small>
        </span>
      </div>
      <div className="voy-waterfall">
        <div className="voy-wf-row voy-wf-row--total">
          <span>Average {noun}</span>
          <div className="voy-wf-track">
            <div className="voy-wf-bar voy-wf-bar--total" style={{ left: 0, width: pct(Math.max(0, base), 2) }} />
          </div>
          <strong>{pct(base)}</strong>
        </div>
        {rows.map((row) => {
          const from = running;
          running += row.value;
          const left = Math.max(0, Math.min(from, running));
          const width = Math.min(1, Math.max(from, running)) - left;
          return (
            <div className="voy-wf-row" key={row.label}>
              <span title={row.label}>{row.label}</span>
              <div className="voy-wf-track">
                <div className={`voy-wf-bar ${row.value >= 0 ? "voy-wf-bar--up" : "voy-wf-bar--down"}`} style={{ left: pct(left, 2), width: pct(Math.max(width, 0.004), 2) }} />
              </div>
              <strong>{points(row.value)}</strong>
            </div>
          );
        })}
        <div className="voy-wf-row voy-wf-row--total">
          <span>This {noun}</span>
          <div className="voy-wf-track">
            <div className="voy-wf-bar voy-wf-bar--final" style={{ left: 0, width: pct(prediction, 2), background: probabilityColor(prediction) }} />
          </div>
          <strong>{pct(prediction)}</strong>
        </div>
      </div>
      <p className="panel-hint voy-wf-note">
        SHAP: each bar is how much that fact moved the probability away from the average {noun}'s, in percentage points. They add up exactly.
      </p>
    </div>
  );
}

function WhatIfPanel({
  scenario,
  accessToken,
  whatIf,
  onChange,
  onClose,
  positiveLabel,
}: {
  scenario: ScenarioSummary;
  accessToken: string | null;
  whatIf: WhatIf;
  onChange: (next: WhatIf) => void;
  onClose: () => void;
  positiveLabel: string;
}) {
  const extras = scenario.ui_extras as VoyageExplorerExtra;
  const featureColumns = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const featureSchema = scenario.feature_schema ?? {};
  const [result, setResult] = useState<{ prediction: number; base: number; items: { feature: string; value: number }[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Live re-score as the form changes (debounced — one small /predict per pause).
  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const response = await predict(config.predictionUrl, scenario.slug, [whatIf.record], accessToken);
        const p = response.predictions[0];
        if (!cancelled) {
          setResult({ prediction: p.prediction, ...explainByFeature(featureColumns, p.prediction, p.contributions) });
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [whatIf.record, scenario.slug, accessToken, featureColumns]);

  const delta = result && whatIf.original ? result.prediction - whatIf.original.prediction : null;
  return (
    <div className="voy-whatif">
      <div className="voy-person-head">
        <div>
          <h3>What if…?</h3>
          <p className="panel-hint">
            Editing {whatIf.source} — the ★ on the ship is this {extras.person_noun}.
          </p>
        </div>
        <button className="btn-secondary" onClick={onClose}>
          Done
        </button>
      </div>
      {result && (
        <>
          {delta !== null && Math.abs(delta) >= 0.001 && (
            <p className="voy-delta">
              {pct(whatIf.original!.prediction)} → <strong>{pct(result.prediction)}</strong>{" "}
              <span className={delta > 0 ? "voy-delta--up" : "voy-delta--down"}>({points(delta)})</span>
            </p>
          )}
          <Waterfall
            scenario={scenario}
            record={whatIf.record}
            base={result.base}
            prediction={result.prediction}
            items={result.items}
            positiveLabel={positiveLabel}
            noun={extras.person_noun}
          />
        </>
      )}
      {error && <p className="error">{error}</p>}
      <div className="feature-grid">
        {featureColumns.map((feature) => (
          <FeatureInput
            key={feature}
            feature={feature}
            spec={featureSchema[feature]}
            value={whatIf.record[feature]}
            onChange={(v) => onChange({ ...whatIf, record: { ...whatIf.record, [feature]: v } })}
          />
        ))}
      </div>
    </div>
  );
}
