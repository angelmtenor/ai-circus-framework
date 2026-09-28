import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { datasetSample, modelCard, predict, type ExampleRecord, type ScenarioSummary, type VoyageExplorerExtra } from "./apiClient";
import { config } from "./config";
import { StatTile } from "./charts";
import { explainByFeature, featureLabel, FeatureInput, initialRecord, isTextFeature, type Record_ } from "./predictUtils";
import { officeTower } from "./officeScene";
import { RubricCheckPanel } from "./RubricCheckPanel";
import { withArticle, type Point, type SceneModule, type ZoneLayout } from "./scenes";
import { ModelDuel, scoreTextRecord, TextFeatureExplanations, textFeatures, type TextScore } from "./textModels";
import { oceanLiner } from "./voyageScene";
import "./voyage.css";

// `ui_extras.scene` -> the illustration people are drawn into (see scenes.ts).
const SCENES: Record<VoyageExplorerExtra["scene"], SceneModule> = { ocean_liner: oceanLiner, office_tower: officeTower };

// Diverging scale for P(positive class): one pole -> neutral gray -> the other pole.
// Validated (dataviz validate_palette.js, dark, scene surface #0b1626): lightness band,
// CVD ΔE 22 (protan), normal-vision ΔE 31, contrast >= 3:1. The positive class is blue
// (good, e.g. "survived") unless the scenario marks it adverse (e.g. "bad leadership").
const BLUE = [63, 147, 235]; // #3f93eb
const RED = [234, 79, 88]; // #ea4f58
const MIDPOINT = [125, 130, 140]; // #7d828c
type Palette = { pos: number[]; neg: number[]; posHex: string; negHex: string };
const GOOD_POSITIVE: Palette = { pos: BLUE, neg: RED, posHex: "#3f93eb", negHex: "#ea4f58" };
const ADVERSE_POSITIVE: Palette = { pos: RED, neg: BLUE, posHex: "#ea4f58", negHex: "#3f93eb" };
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
  at: Point;
};

// A person's SHAP explanation, fetched when they are clicked (the bulk load scores
// everyone without SHAP — TreeExplainer costs ~5 ms per row). Text scenarios also get
// word-level highlights and the transformer challenger's score.
type Detail = { base: number; items: { feature: string; value: number }[]; score: TextScore | null };

type WhatIf = { source: string; original: Person | null; record: Record_ };

type Loaded = {
  people: Person[];
  layout: ZoneLayout;
  radius: number;
  spare: Point[];
  truncated: { shown: number; total: number } | null;
  auc: number | null;
};

// Session cache (module scope): one scored, berthed passenger list per scenario+caller.
const VOYAGE_CACHE = new Map<string, Promise<Loaded>>();

async function loadVoyage(
  scene: SceneModule,
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
  // Probabilities only: each person is explained when clicked (see Detail).
  const response = await predict(config.predictionUrl, scenario.slug, records, accessToken, { explain: false });
  const people = rows.map((row, index) => {
    const prediction = response.predictions[index].prediction;
    return {
      index,
      id: sample.id_column ? String(row[sample.id_column]) : String(index + 1),
      name: extras.name_column ? String(row[extras.name_column] ?? "") : null,
      record: records[index],
      actual: Number(row[target]) === 1 ? 1 : 0,
      zone: zoneIndex.get(String(row[extras.zone_by]))!,
      prediction,
      at: { x: 0, y: 0 },
    } satisfies Person;
  });
  // Berth: within each zone, most-likely survivors first — the zone reads as a gradient.
  const byZone = extras.zones.map((_, z) => people.filter((p) => p.zone === z).sort((a, b) => b.prediction - a.prediction));
  const packed = scene.packZones(byZone.map((z) => z.length));
  byZone.forEach((zonePeople, z) => zonePeople.forEach((p, i) => (p.at = packed.slots[z][i])));
  const card = await cardPromise;
  return {
    people,
    layout: packed,
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

function probabilityColor(p: number, palette: Palette): string {
  return p < 0.5 ? mix(palette.neg, MIDPOINT, p / 0.5) : mix(MIDPOINT, palette.pos, (p - 0.5) / 0.5);
}

/** Explain one record: SHAP by feature, plus words + challenger for text scenarios. */
async function explainRecord(scenario: ScenarioSummary, record: Record_, accessToken: string | null, withChallenger: boolean): Promise<Detail> {
  const featureColumns = scenario.feature_columns ?? [];
  if (textFeatures(scenario).length > 0) {
    const score = await scoreTextRecord(scenario, record, accessToken, withChallenger);
    return { ...explainByFeature(featureColumns, score.champion.prediction, score.champion.contributions), score };
  }
  const response = await predict(config.predictionUrl, scenario.slug, [record], accessToken);
  const p = response.predictions[0];
  return { ...explainByFeature(featureColumns, p.prediction, p.contributions), score: null };
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
 * `titanic` (the `ocean_liner` scene) and `toxic_leadership` (`office_tower`). Every
 * real row is drawn as one person inside the scene (see scenes.ts), seated by
 * `zone_by`, scored in one batched /predict call (probabilities only) and coloured by
 * it; a clicked person is explained on demand. The scenario's wording fields and the
 * scene's copy are the only domain vocabulary here.
 */
export function VoyageView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as VoyageExplorerExtra;
  const scene = SCENES[extras.scene] ?? oceanLiner;
  const palette = extras.positive_is_adverse ? ADVERSE_POSITIVE : GOOD_POSITIVE;
  const textColumn = textFeatures(scenario)[0] ?? null;
  const featureColumns = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const featureSchema = useMemo(() => scenario.feature_schema ?? {}, [scenario.feature_schema]);
  const target = scenario.target ?? "";
  const positiveLabel = scenario.target_value_labels?.["1"] ?? "Positive";
  const negativeLabel = scenario.target_value_labels?.["0"] ?? "Negative";
  const noun = extras.person_noun;
  const zoneIndex = useMemo(() => new Map(extras.zones.map((z, i) => [z.key, i])), [extras.zones]);
  const categoricalFilters = useMemo(() => [extras.zone_by, ...extras.filters.filter((f) => f !== extras.zone_by)], [extras]);

  const [people, setPeople] = useState<Person[] | null>(null);
  const [layout, setLayout] = useState<ZoneLayout | null>(null);
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
      pending = loadVoyage(scene, scenario, extras, featureColumns, zoneIndex, target, accessToken);
      VOYAGE_CACHE.set(key, pending);
    }
    pending
      .then((loaded) => {
        if (cancelled) return;
        setLayout(loaded.layout);
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
  }, [scene, scenario, accessToken, featureColumns, extras, zoneIndex, target]);

  // The selected person's explanation, fetched on click and kept for the session view.
  const details = useRef(new Map<number, Promise<Detail>>());
  const [detail, setDetail] = useState<{ index: number; value: Detail | null; error: string | null } | null>(null);
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    let pending = details.current.get(selected.index);
    if (!pending) {
      pending = explainRecord(scenario, selected.record, accessToken, Boolean(scenario.text_challenger));
      details.current.set(selected.index, pending);
    }
    setDetail({ index: selected.index, value: null, error: null });
    pending
      .then((value) => !cancelled && setDetail({ index: selected.index, value, error: null }))
      .catch((e) => {
        details.current.delete(selected.index);
        if (!cancelled) setDetail({ index: selected.index, value: null, error: (e as Error).message });
      });
    return () => {
      cancelled = true;
    };
  }, [selected, scenario, accessToken]);
  const selectedDetail = selected && detail?.index === selected.index ? detail : null;

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
        ? {
            id: selected.id,
            name: selected.name,
            features: selected.record,
            probability: selected.prediction,
            actual: selected.actual ? positiveLabel : negativeLabel,
            explanation: selectedDetail?.value?.items.slice(0, 6) ?? null,
            challenger_probability: selectedDetail?.value?.score?.challenger?.prediction ?? null,
          }
        : null,
      cohort: { filters: Object.fromEntries(Object.entries(chips).map(([f, s]) => [f, [...s]])), ranges, ...cohort },
    },
  });

  const probe = useRef<SVGSVGElement>(null);
  function personAt(event: MouseEvent<SVGSVGElement>): Person | null {
    const svg = probe.current;
    if (!svg || !people) return null;
    const box = svg.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * scene.width;
    const y = ((event.clientY - box.top) / box.height) * scene.height;
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
          <span className="voy-loading-ship">{scene.copy.loadingIcon}</span>
          {scene.copy.loading(noun)}
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
    <div className={`tab-panel voy${extras.positive_is_adverse ? " voy--adverse" : ""}`}>
      <div className="panel-card voy-header">
        <div>
          <h3>{extras.title}</h3>
          {extras.subtitle && <p className="panel-hint">{extras.subtitle}</p>}
        </div>
        <div className="kpi-row">
          <StatTile label={`${noun[0].toUpperCase()}${noun.slice(1)}s ${scene.copy.aboard}`} value={people.length.toLocaleString()} />
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
            {scene.copy.reveal}
          </button>
          <button className={mode === "surprises" ? "active" : ""} onClick={() => setMode("surprises")}>
            Model vs reality
          </button>
        </div>
        <div className="voy-search">
          <input
            type="search"
            placeholder={`Find ${withArticle(noun)} by ${extras.name_column ? humanize(featureLabel(scenario, extras.name_column)) : "name"} or id…`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label={`Find ${withArticle(noun)}`}
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
          viewBox={`0 0 ${scene.width} ${scene.height}`}
          role="img"
          aria-label={`${people.length} ${noun}s ${scene.copy.aboard}, coloured by ${mode === "model" ? "predicted probability" : "real outcome"}`}
          onMouseMove={(e) => setHovered(personAt(e))}
          onMouseLeave={() => setHovered(null)}
          onClick={(e) => {
            const person = personAt(e);
            if (person) select(person);
          }}
        >
          {layout && <scene.Backdrop layout={layout} zoneLabels={extras.zones.map((z) => z.label)} />}
          <g className="voy-people">
            {people.map((p) => {
              const dim = filtering && !matches.has(p.index);
              const wrong = (p.prediction >= 0.5 ? 1 : 0) !== p.actual;
              let fill = probabilityColor(p.prediction, palette);
              let stroke = "none";
              if (mode === "fate") {
                // Secondary encoding: the negative outcome is also a hollow ring, never colour alone.
                fill = p.actual ? palette.posHex : "transparent";
                stroke = p.actual ? "none" : palette.negHex;
              } else if (mode === "surprises") {
                fill = wrong ? (p.actual ? palette.posHex : palette.negHex) : CORRECT_HEX;
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
                  // Each scene has its own reveal sweep (the liner: bow to stern).
                  style={{ transitionDelay: `${scene.revealDelay(p.at)}s` }}
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
          <scene.Foreground nameplate={extras.title.split("·")[0].trim().toUpperCase()} />
        </svg>
        {hovered && (
          <div
            className="voy-tooltip"
            style={{ left: `${(hovered.at.x / scene.width) * 100}%`, top: `${(hovered.at.y / scene.height) * 100}%` }}
          >
            <strong>{hovered.name || `#${hovered.id}`}</strong>
            <span>{[extras.zones[hovered.zone].label, ...extras.filters.map((f) => String(hovered.record[f]))].join(" · ")}</span>
            {textColumn && <em className="voy-tooltip-text">“{snippet(String(hovered.record[textColumn] ?? ""))}”</em>}
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
          <CohortSummary
            cohort={cohort}
            noun={noun}
            positiveLabel={positiveLabel}
            filtering={filtering}
            revealed={mode !== "model"}
            everyone={scene.copy.everyone}
            reveal={scene.copy.reveal}
          />
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
              palette={palette}
              star={scene.copy.star}
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
              {selectedDetail?.value ? (
                <>
                  {selectedDetail.value.score && scenario.text_challenger && <ModelDuel scenario={scenario} score={selectedDetail.value.score} />}
                  <Waterfall
                    scenario={scenario}
                    record={selected.record}
                    base={selectedDetail.value.base}
                    prediction={selected.prediction}
                    items={selectedDetail.value.items}
                    positiveLabel={positiveLabel}
                    noun={noun}
                    palette={palette}
                  />
                  {selectedDetail.value.score && (
                    <TextFeatureExplanations scenario={scenario} record={selected.record} result={selectedDetail.value.score.champion} />
                  )}
                </>
              ) : selectedDetail?.error ? (
                <p className="error">{selectedDetail.error}</p>
              ) : (
                <div className="app-loading">Asking the model why…</div>
              )}
              <button
                className="btn-primary"
                onClick={() => setWhatIf({ source: selected.name || `#${selected.id}`, original: selected, record: { ...selected.record } })}
              >
                What if…? Edit this {noun}
              </button>
            </>
          ) : (
            <>
              <h3>Pick {withArticle(noun)}</h3>
              <p className="panel-hint">{scene.copy.pickHint(noun)}</p>
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
      {scenario.rubric_check && <RubricCheckPanel scenario={scenario} accessToken={accessToken} />}
    </div>
  );
}

function CohortSummary({
  cohort,
  noun,
  positiveLabel,
  filtering,
  revealed,
  everyone,
  reveal,
}: {
  cohort: { n: number; actualRate: number; predictedRate: number; surprises: number };
  noun: string;
  positiveLabel: string;
  filtering: boolean;
  revealed: boolean;
  everyone: string;
  reveal: string;
}) {
  const label = positiveLabel.toLowerCase();
  return (
    <div className="voy-cohort">
      <h4>{filtering ? `This cohort: ${cohort.n} ${noun}s` : `${everyone}: ${cohort.n} ${noun}s`}</h4>
      {cohort.n === 0 ? (
        <p className="panel-hint">Nobody matches — loosen a filter.</p>
      ) : (
        <>
          <Meter label="Model expects" value={cohort.predictedRate} className="voy-meter--model" />
          <Meter label={`Really ${label}`} value={revealed ? cohort.actualRate : null} className="voy-meter--real" />
          <p className="panel-hint">
            {revealed
              ? `Average model probability ${pct(cohort.predictedRate)}; real rate ${pct(cohort.actualRate)}. At the 50% line the model got ${cohort.n - cohort.surprises} of ${cohort.n} right and misjudged ${cohort.surprises}.`
              : `Switch to “${reveal}” to compare with what really happened (“${label}”).`}
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
  palette,
}: {
  scenario: ScenarioSummary;
  record: Record_;
  base: number;
  prediction: number;
  items: { feature: string; value: number }[];
  positiveLabel: string;
  noun: string;
  palette: Palette;
}) {
  // A free-text feature is shown as "Review (the words)" — its words are highlighted below.
  const valueOf = (feature: string) => (isTextFeature(scenario, feature) ? "(the words)" : `= ${record[feature]}`);
  const shown = items.slice(0, 6);
  const rest = items.slice(6).reduce((s, i) => s + i.value, 0);
  const rows = [...shown.map((i) => ({ label: `${featureLabel(scenario, i.feature)} ${valueOf(i.feature)}`, value: i.value })), ...(items.length > 6 ? [{ label: "All other features", value: rest }] : [])];
  let running = base;
  const ups = shown.filter((i) => i.value > 0.005).slice(0, 2);
  const downs = shown.filter((i) => i.value < -0.005).slice(0, 2);
  const phrase = (list: typeof shown) => list.map((i) => `${featureLabel(scenario, i.feature).toLowerCase()} ${valueOf(i.feature)} (${points(i.value)})`).join(" and ");
  return (
    <div className="voy-explain">
      <div className="voy-explain-headline">
        <span className="voy-explain-prob" style={{ color: probabilityColor(prediction, palette) }}>
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
            <div className="voy-wf-bar voy-wf-bar--final" style={{ left: 0, width: pct(prediction, 2), background: probabilityColor(prediction, palette) }} />
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
  palette,
  star,
}: {
  scenario: ScenarioSummary;
  accessToken: string | null;
  whatIf: WhatIf;
  onChange: (next: WhatIf) => void;
  onClose: () => void;
  positiveLabel: string;
  palette: Palette;
  star: string;
}) {
  const extras = scenario.ui_extras as VoyageExplorerExtra;
  const featureColumns = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const featureSchema = scenario.feature_schema ?? {};
  const hasText = textFeatures(scenario).length > 0;
  const [result, setResult] = useState<{ prediction: number; base: number; items: { feature: string; value: number }[]; score: TextScore | null } | null>(
    null,
  );
  const [challenger, setChallenger] = useState<TextScore | "loading" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Live re-score as the form changes (debounced — one small /predict per pause; longer
  // for typed text). The transformer challenger (a live embedding call) runs on demand.
  useEffect(() => {
    let cancelled = false;
    setChallenger(null);
    const timer = window.setTimeout(
      async () => {
        try {
          const detail = await explainRecord(scenario, whatIf.record, accessToken, false);
          if (!cancelled) {
            setResult({ prediction: detail.score?.champion.prediction ?? detail.base + detail.items.reduce((s, i) => s + i.value, 0), ...detail });
            setError(null);
          }
        } catch (e) {
          if (!cancelled) setError((e as Error).message);
        }
      },
      hasText ? 500 : 250,
    );
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [whatIf.record, scenario, accessToken, hasText]);

  async function compare() {
    setChallenger("loading");
    try {
      setChallenger(await scoreTextRecord(scenario, whatIf.record, accessToken, true));
    } catch (e) {
      setChallenger(null);
      setError((e as Error).message);
    }
  }

  const delta = result && whatIf.original ? result.prediction - whatIf.original.prediction : null;
  return (
    <div className="voy-whatif">
      <div className="voy-person-head">
        <div>
          <h3>What if…?</h3>
          <p className="panel-hint">
            Editing {whatIf.source} — {star} is this {extras.person_noun}.
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
          {scenario.text_challenger &&
            (challenger && challenger !== "loading" ? (
              <ModelDuel scenario={scenario} score={challenger} />
            ) : (
              <button className="btn-secondary voy-compare" onClick={compare} disabled={challenger === "loading"}>
                {challenger === "loading" ? "Embedding the text…" : `⚖ Compare with the transformer (${scenario.text_challenger.label})`}
              </button>
            ))}
          <Waterfall
            scenario={scenario}
            record={whatIf.record}
            base={result.base}
            prediction={result.prediction}
            items={result.items}
            positiveLabel={positiveLabel}
            noun={extras.person_noun}
            palette={palette}
          />
          {result.score && <TextFeatureExplanations scenario={scenario} record={whatIf.record} result={result.score.champion} />}
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

/** "JobTitle" -> "job title" (display columns have no schema label). */
function humanize(name: string): string {
  return name.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ").toLowerCase();
}

/** The first words of a long text, for tooltips. */
function snippet(text: string, max = 110): string {
  return text.length <= max ? text : `${text.slice(0, max).replace(/\s+\S*$/, "")}…`;
}
