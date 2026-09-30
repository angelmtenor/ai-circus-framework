import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import worldLandUrl from "world-atlas/land-110m.json?url";
import type { Topology } from "topojson-specification";
import { datasetSample, modelCard, networkGraph, outOfFoldScores, predict, type MoneyTrailExtra, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { fetchJsonOnce, CATEGORICAL, useElementSize, usePrefersReducedMotion } from "./logisticsViz";
import {
  backtest,
  buildModel,
  counterparties,
  dayLabel,
  factColumns,
  hourLabel,
  hourlySeries,
  networkExplorerExtras,
  money,
  type Model,
  type MtFlow,
  type MtNode,
  pct,
  percentileIn,
  type RowInput,
  tierOf,
  typologyStats,
} from "./moneyTrailModel";
import { FORMAT_SHAPES, type MtColors, MoneyTrailScene, shapeSvg } from "./moneyTrailScene";
import { explainByFeature, featureLabel } from "./predictUtils";
import { NETWORK_PALETTE, RISK_RAMP, surfaceMode, TIER_GLYPHS, tierColor } from "./riskPalette";
import { useTheme } from "./useTheme";
import "./moneyTrail.css";

const NetworkExplorerView = lazy(() => import("./NetworkExplorerView").then((m) => ({ default: m.NetworkExplorerView })));

/**
 * Generic 5th workspace tab for a binary-classification tabular_ml scenario whose rows
 * are account holders (people and legal entities) of a set of banks and whose positive
 * class is involvement in laundering (`ui_extras: {kind: money_trail}`, see
 * scenario_schema.MoneyTrailExtra) — e.g. `aml_money_trail`.
 *
 * Every holder carries its out-of-fold score (`model.out_of_fold_scores`: from a model
 * that never saw it) and is sorted into the scenario's tiers. A world map clusters the
 * holders around their banks and replays the scenario's network hour by hour — one
 * particle per payment, its *shape* the payment format — next to a ledger of that hour's
 * biggest transfers and a watchlist of the riskiest holders on the map. Picking a holder
 * draws all of its flows and opens a dossier (SHAP rolled into pillars, percentiles,
 * counterparties, an hour-by-hour strip). The real outcome can be revealed as a
 * backtest, with a gallery of the laundering typologies and how many of each the model
 * flags — honest, because the scores are cross-fitted.
 *
 * Colour: tiers use the shared validated ordinal red ramp (riskPalette.ts) with the
 * lowest tier recessive grey and a glyph + label per tier; payment format is a shape;
 * a selected holder's payments sent / received use the first two categorical slots and
 * an arrowhead, so direction never rests on colour alone.
 */

type Loaded = {
  rows: RowInput[];
  model: Model;
  cvAuc: number | null;
  holdoutAuc: number | null;
  oofAuc: number | null;
  crossFitted: boolean;
};
type Detail = { base: number; deployed: number; items: { feature: string; value: number }[] };

const CACHE = new Map<string, Promise<Loaded>>();
const SPEEDS = [1, 2, 4];
const TICK_MS = 620;
const LEDGER_ROWS = 24;
const WATCH_ROWS = 14;

async function loadMoneyTrail(scenario: ScenarioSummary, extras: MoneyTrailExtra, accessToken: string | null): Promise<Loaded> {
  const url = config.predictionUrl;
  const features = scenario.feature_columns ?? [];
  const [sample, graph, oof, card] = await Promise.all([
    datasetSample(url, scenario.slug, 30000, accessToken),
    networkGraph(url, scenario.slug, accessToken),
    outOfFoldScores(url, scenario.slug, accessToken).catch(() => null),
    modelCard(url, scenario.slug, accessToken).catch(() => null),
  ]);
  const idOf = (row: Record<string, unknown>, index: number) => (sample.id_column ? String(row[sample.id_column]) : String(index + 1));
  const records = sample.rows.map((row) => Object.fromEntries(features.map((f) => [f, row[f] as number | string])));
  const blind = new Map((oof?.rows ?? []).map((r) => [r.id, r.probability]));
  const crossFitted = sample.rows.length > 0 && sample.rows.every((row, i) => blind.has(idOf(row, i)));
  // Fallback (scores not trained yet): the deployed model's own, in-sample scores.
  const inSample = crossFitted ? null : await predict(url, scenario.slug, records, accessToken, { explain: false });
  const target = scenario.target ?? "";
  const dash = (v: unknown) => (v === null || v === undefined || v === "" || v === "—" ? null : String(v));
  const rows = sample.rows.map((row, index): RowInput => {
    const probability = crossFitted ? (blind.get(idOf(row, index)) as number) : inSample!.predictions[index].prediction;
    return {
      id: idOf(row, index),
      name: String(row[extras.name_column] ?? ""),
      detail: extras.detail_columns.map((c) => row[c]).filter((v) => v !== null && v !== "").join(" · "),
      person: extras.person_types.includes(String(row[extras.holder_type_feature] ?? "")),
      display: row,
      record: records[index],
      probability,
      tier: tierOf(probability, extras.tiers),
      actual: Number(row[target]) === 1 ? 1 : 0,
      size: extras.size_feature ? Number(row[extras.size_feature]) : 1,
      typology: extras.outcome_detail_column ? dash(row[extras.outcome_detail_column]) : null,
      caseId: extras.case_column ? dash(row[extras.case_column]) : null,
    };
  });
  return {
    rows,
    model: buildModel(graph, rows, extras),
    cvAuc: card?.metrics.cv_roc_auc_mean ?? null,
    holdoutAuc: card?.metrics.holdout_roc_auc ?? null,
    oofAuc: crossFitted ? oof?.roc_auc ?? null : null,
    crossFitted,
  };
}

function points(v: number): string {
  return `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pts`;
}

function featureValue(value: string | number): string {
  if (typeof value !== "number") return String(value);
  if (Math.abs(value) >= 10000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return String(Number(value.toFixed(2)));
}

function colorsFor(mode: "dark" | "light", nTiers: number): MtColors {
  const net = NETWORK_PALETTE[mode];
  const cat = CATEGORICAL[mode];
  return {
    oceanTop: mode === "dark" ? "#0b1a33" : "#eef4fb",
    oceanBottom: mode === "dark" ? "#050b18" : "#dbe6f3",
    land: mode === "dark" ? "#16294a" : "#fdfefe",
    border: mode === "dark" ? "rgba(150,180,220,0.22)" : "rgba(40,70,110,0.22)",
    graticule: mode === "dark" ? "rgba(127,163,201,0.07)" : "rgba(22,32,43,0.06)",
    ink: net.ink,
    dim: mode === "dark" ? "#9fb3cc" : "#4e6378",
    halo: mode === "dark" ? "#050b18" : "#f6f8fb",
    bank: net.entity,
    money: mode === "dark" ? "#cfe3ff" : "#1b3a63",
    out: cat[1],
    in: cat[0],
    scheme: cat[3],
    context: net.context,
    ring: RISK_RAMP[mode].ring,
    tiers: Array.from({ length: nTiers }, (_, i) => tierColor(mode, i)),
  };
}

/** A small node-link diagram of one laundering typology. */
const MOTIFS: Record<string, { nodes: [number, number][]; edges: [number, number][] }> = {
  "Fan-out": { nodes: [[12, 28], [88, 6], [88, 17], [88, 28], [88, 39], [88, 50]], edges: [[0, 1], [0, 2], [0, 3], [0, 4], [0, 5]] },
  "Fan-in": { nodes: [[88, 28], [12, 6], [12, 17], [12, 28], [12, 39], [12, 50]], edges: [[1, 0], [2, 0], [3, 0], [4, 0], [5, 0]] },
  Cycle: {
    nodes: [[50, 6], [76, 20], [66, 46], [34, 46], [24, 20]],
    edges: [[0, 1], [1, 2], [2, 3], [3, 4], [4, 0]],
  },
  Bipartite: {
    nodes: [[16, 8], [16, 28], [16, 48], [84, 8], [84, 28], [84, 48]],
    edges: [[0, 3], [0, 4], [1, 4], [1, 5], [2, 3], [2, 5]],
  },
  "Scatter-gather": {
    nodes: [[8, 28], [50, 7], [50, 21], [50, 35], [50, 49], [92, 28]],
    edges: [[0, 1], [0, 2], [0, 3], [0, 4], [1, 5], [2, 5], [3, 5], [4, 5]],
  },
  "Gather-scatter": {
    nodes: [[8, 8], [8, 28], [8, 48], [50, 28], [92, 8], [92, 28], [92, 48]],
    edges: [[0, 3], [1, 3], [2, 3], [3, 4], [3, 5], [3, 6]],
  },
  Stack: {
    nodes: [[14, 16], [14, 40], [50, 16], [50, 40], [86, 16], [86, 40]],
    edges: [[0, 2], [0, 3], [1, 2], [1, 3], [2, 4], [2, 5], [3, 4], [3, 5]],
  },
  Random: {
    nodes: [[14, 12], [42, 8], [70, 18], [86, 42], [56, 46], [26, 40]],
    edges: [[0, 1], [1, 3], [2, 4], [4, 5], [5, 0], [1, 4], [3, 2]],
  },
  // Laundering outside the eight textbook shapes: just the involved holders, no pattern.
  Unpatterned: { nodes: [[16, 14], [40, 40], [62, 12], [84, 34], [50, 24]], edges: [] },
}; // fmt: skip

function Motif({ name, color }: { name: string; color: string }) {
  const motif = MOTIFS[name] ?? MOTIFS.Random;
  return (
    <svg viewBox="0 0 100 56" className="mt-motif" aria-hidden>
      <defs>
        <marker id={`mt-arrow-${name}`} viewBox="0 0 6 6" refX="5.5" refY="3" markerWidth="5" markerHeight="5" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill={color} />
        </marker>
      </defs>
      {motif.edges.map(([a, b], i) => {
        const [x1, y1] = motif.nodes[a];
        const [x2, y2] = motif.nodes[b];
        const len = Math.hypot(x2 - x1, y2 - y1) || 1;
        const ux = (x2 - x1) / len;
        const uy = (y2 - y1) / len;
        return (
          <line
            key={i}
            x1={x1 + ux * 4.5}
            y1={y1 + uy * 4.5}
            x2={x2 - ux * 5.5}
            y2={y2 - uy * 5.5}
            stroke={color}
            strokeWidth={1.3}
            opacity={0.85}
            markerEnd={`url(#mt-arrow-${name})`}
          />
        );
      })}
      {motif.nodes.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={3.6} fill={color} />
      ))}
    </svg>
  );
}

function FormatMark({ kind, size = 7, color = "currentColor" }: { kind: string; size?: number; color?: string }) {
  const shape = shapeSvg(FORMAT_SHAPES[kind] ?? "circle", 8, 8, size / 2 + 1);
  const stroke = (FORMAT_SHAPES[kind] ?? "circle") === "cross";
  return (
    <svg width={16} height={16} viewBox="0 0 16 16" aria-hidden className="mt-mark">
      {shape.points ? <polygon points={shape.points} fill={color} /> : <path d={shape.d} fill={stroke ? "none" : color} stroke={stroke ? color : "none"} strokeWidth={stroke ? 2 : 0} strokeLinecap="round" />}
    </svg>
  );
}

export function MoneyTrailView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as MoneyTrailExtra;
  const { theme } = useTheme();
  const mode = surfaceMode(theme.cssVars["--bg"]);
  const reducedMotion = usePrefersReducedMotion();
  const noun = extras.entity_noun;
  const features = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const flagTier = useMemo(() => {
    const i = extras.tiers.findIndex((t) => t.label === extras.flag_from_tier);
    return i >= 0 ? i : extras.tiers.length - 1;
  }, [extras]);
  const colors = useMemo(() => colorsFor(mode, extras.tiers.length), [mode, extras.tiers.length]);
  const formatLabel = useMemo(() => new Map(extras.flows.map((f) => [f.kind, f.label])), [extras.flows]);

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hour, setHour] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [revealed, setRevealed] = useState(false);
  const [typology, setTypology] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [hover, setHover] = useState<{ node: MtNode; x: number; y: number } | null>(null);
  const [geometryReady, setGeometryReady] = useState(false);
  const [view, setView] = useState<"map" | "graph">("map");
  const [graphOpened, setGraphOpened] = useState(false);
  const graphScenario = useMemo(() => ({ ...scenario, ui_extras: networkExplorerExtras(extras) }), [scenario, extras]);

  useEffect(() => {
    const key = `${scenario.slug}|${accessToken ?? ""}`;
    if (!CACHE.has(key)) CACHE.set(key, loadMoneyTrail(scenario, extras, accessToken));
    let cancelled = false;
    CACHE.get(key)!
      .then((d) => !cancelled && setLoaded(d))
      .catch((e: Error) => {
        CACHE.delete(key);
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [scenario, extras, accessToken]);

  const model = loaded?.model ?? null;
  const rows = useMemo(() => loaded?.rows ?? [], [loaded]);
  const nHours = model?.periods.length ?? 0;
  const tickMs = (reducedMotion ? 1100 : TICK_MS) / speed;

  const stats = useMemo(() => backtest(rows, extras.tiers.length, flagTier), [rows, extras.tiers.length, flagTier]);
  const typologies = useMemo(() => typologyStats(rows, flagTier), [rows, flagTier]);
  const factSorted = useMemo(() => factColumns(rows, extras.facts), [rows, extras.facts]);
  const onMap = useMemo(() => (model?.nodes ?? []).filter((n) => n.kind === "row" && n.holder), [model]);
  const watchlist = useMemo(() => [...onMap].sort((a, b) => b.holder!.probability - a.holder!.probability).slice(0, WATCH_ROWS), [onMap]);
  const selected = selectedId && model ? model.byId.get(selectedId) ?? null : null;

  // The hour's (or the ten days') flows, biggest first.
  const window_ = useMemo(() => {
    if (!model) return { flows: [] as { flow: MtFlow; usd: number }[], usd: 0, count: 0, crossBorder: 0 };
    const list =
      hour === null
        ? [...model.flows].sort((a, b) => b.weight - a.weight).map((flow) => ({ flow, usd: flow.weight }))
        : (model.flowsByHour[hour] ?? []).map((i) => ({ flow: model.flows[i], usd: model.flows[i].hours[hour] }));
    const cross = list.filter(({ flow }) => flow.source.country !== flow.target.country).length;
    return {
      flows: list,
      usd: hour === null ? model.hourlyUsd.reduce((a, b) => a + b, 0) : model.hourlyUsd[hour],
      count: list.length,
      crossBorder: list.length ? cross / list.length : 0,
    };
  }, [model, hour]);

  const highlight = useMemo(() => {
    if (!typology || !revealed) return null;
    return new Set(onMap.filter((n) => n.holder!.typology === typology).map((n) => n.id));
  }, [typology, revealed, onMap]);

  // ── the map ──────────────────────────────────────────────────────────────────
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<MoneyTrailScene | null>(null);
  const [stageBox, stageSize] = useElementSize<HTMLDivElement>();
  const selectRef = useRef<(id: string | null) => void>(() => undefined);
  useEffect(() => {
    selectRef.current = (id) => setSelectedId((cur) => (id === cur ? null : id));
  }, []);

  useEffect(() => {
    if (!canvas.current || !loaded) return;
    const s = new MoneyTrailScene(canvas.current, {
      onSelect: (id) => selectRef.current(id),
      onHover: (node, x, y) => setHover(node ? { node, x, y } : null),
    });
    scene.current = s;
    s.setModel(loaded.model, extras);
    return () => s.destroy();
  }, [loaded, extras]);
  useEffect(() => {
    let cancelled = false;
    fetchJsonOnce<Topology>(worldLandUrl)
      .then((land) => {
        if (cancelled || !scene.current) return;
        scene.current.setGeometry(land);
        setGeometryReady(true);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [loaded]);
  useEffect(() => scene.current?.resize(stageSize.width, stageSize.height), [stageSize, loaded, geometryReady]);
  useEffect(() => {
    scene.current?.setView({ mode, colors, hour, playing, tickMs, selectedId, revealed, flagTier, highlight, reducedMotion });
  }, [mode, colors, hour, playing, tickMs, selectedId, revealed, flagTier, highlight, reducedMotion, loaded, geometryReady]);

  // ── replay ───────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!playing || nHours === 0) return;
    const id = window.setInterval(() => setHour((h) => Math.min(nHours - 1, h === null ? 0 : h + 1)), tickMs);
    return () => window.clearInterval(id);
  }, [playing, nHours, tickMs]);
  useEffect(() => {
    if (playing && hour === nHours - 1) setPlaying(false);
  }, [playing, hour, nHours]);
  const togglePlay = () => {
    if (playing) return setPlaying(false);
    setHour((h) => (h === null || h >= nHours - 1 ? 0 : h));
    setPlaying(true);
  };

  // ── the dossier's explanation ────────────────────────────────────────────────
  useEffect(() => {
    setDetail(null);
    setDetailError(null);
    const holder = selected?.holder;
    if (!holder) return;
    let cancelled = false;
    predict(config.predictionUrl, scenario.slug, [holder.record], accessToken)
      .then((response) => {
        if (cancelled) return;
        const p = response.predictions[0];
        setDetail({ ...explainByFeature(features, p.prediction, p.contributions), deployed: p.prediction });
      })
      .catch((e: Error) => !cancelled && setDetailError(e.message));
    return () => {
      cancelled = true;
    };
  }, [selected, scenario.slug, accessToken, features]);

  const pillars = useMemo(() => {
    if (!detail) return [];
    const value = new Map(detail.items.map((i) => [i.feature, i.value]));
    return extras.pillars
      .map((p) => ({ label: p.label, value: p.features.reduce((sum, f) => sum + (value.get(f) ?? 0), 0) }))
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  }, [detail, extras.pillars]);

  const selectedFlows = useMemo(() => (model && selected ? counterparties(model, selected.id) : []), [model, selected]);
  const selectedSeries = useMemo(() => (model && selected ? hourlySeries(model, selected.id) : null), [model, selected]);

  useCopilotReadable({
    description: `${scenario.title} money trail: every ${noun} scored for "${extras.outcome_label}", the replay hour the user is looking at, and the ${noun} they selected with its flows. Use it to explain who looks like a laundering profile now and why.`,
    value:
      loaded && model
        ? {
            replay: hour === null ? "all ten days" : hourLabel(model.periods[hour]),
            payments_in_view: window_.count,
            usd_in_view: Math.round(window_.usd),
            biggest_payments: window_.flows.slice(0, 6).map(({ flow, usd }) => ({ from: flow.source.label, to: flow.target.label, format: flow.kind, usd: Math.round(usd) })),
            riskiest_on_map: watchlist.slice(0, 6).map((n) => ({ holder: n.label, country: n.country, probability: Number(n.holder!.probability.toFixed(3)) })),
            selected: selected?.holder
              ? {
                  holder: selected.label,
                  country: selected.country,
                  probability: Number(selected.holder.probability.toFixed(3)),
                  tier: extras.tiers[selected.holder.tier].label,
                  top_drivers: detail?.items.slice(0, 5).map((i) => ({ feature: featureLabel(scenario, i.feature), points: Number((i.value * 100).toFixed(1)) })),
                  counterparties: selectedFlows.slice(0, 5).map((c) => ({ holder: c.node.label, direction: c.direction, usd: Math.round(c.usd) })),
                  ...(revealed ? { actual: selected.holder.actual ? extras.outcome_label : "not involved", typology: selected.holder.typology } : {}),
                }
              : null,
            outcomes_revealed: revealed,
          }
        : null,
  });

  if (error) return <div className="mt-empty">Could not load the money trail: {error}</div>;
  if (!loaded || !model) {
    return (
      <div className="mt-empty mt-loading">
        <span className="mt-spinner" /> Scoring every {noun}…
      </div>
    );
  }

  const maxHour = Math.max(1, ...model.hourlyUsd);
  const selectedHolder = selected?.holder ?? null;
  const days = Array.from({ length: Math.ceil(nHours / 24) }, (_, d) => d);
  const clock = hour === null ? `${dayLabel(model.periods[0])} – ${dayLabel(model.periods[nHours - 1])}` : hourLabel(model.periods[hour]);
  const flaggedOnMap = onMap.filter((n) => n.holder!.tier >= flagTier).length;
  const persons = onMap.filter((n) => n.person).length;

  return (
    <div className={`mt mt--${mode}`}>
      <div className="mt-views" role="tablist" aria-label="View">
        {(
          [
            ["map", "World map"],
            ["graph", "Network graph"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={view === key}
            className={view === key ? "mt-views--on" : ""}
            onClick={() => {
              setView(key);
              if (key === "graph") {
                setGraphOpened(true);
                setPlaying(false);
              }
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {graphOpened && (
        <div className="mt-graph-view" hidden={view !== "graph"}>
          <Suspense fallback={<div className="mt-empty">Loading the network…</div>}>
            <NetworkExplorerView scenario={graphScenario} accessToken={accessToken} />
          </Suspense>
        </div>
      )}
      <div className="mt-map-view" hidden={view !== "map"}>
      <header className="mt-head">
        <div>
          <h3>{extras.title}</h3>
          {extras.subtitle && <p>{extras.subtitle}</p>}
        </div>
        <div className="mt-model">
          {loaded.cvAuc !== null && (
            <span title="Out-of-sample ranking quality: cross-validated ROC AUC (1.0 = perfect ordering, 0.5 = chance).">
              ROC AUC <b>{loaded.cvAuc.toFixed(3)}</b> <em>cross-validated</em>
            </span>
          )}
          {loaded.holdoutAuc !== null && (
            <span>
              hold-out <b>{loaded.holdoutAuc.toFixed(3)}</b>
            </span>
          )}
          {loaded.oofAuc !== null && (
            <span title={`ROC AUC of the cross-fitted scores drawn on this tab — every ${noun} scored by a model that never saw it.`}>
              on the map <b>{loaded.oofAuc.toFixed(3)}</b> <em>out-of-fold</em>
            </span>
          )}
        </div>
      </header>

      <div className="mt-kpis">
        <div className="mt-kpi mt-kpi--clock">
          <span>{hour === null ? "Replay window" : "Replaying"}</span>
          <b className="mt-clock">{clock}</b>
          <em>{hour === null ? "press play to watch the money move" : `hour ${hour + 1} of ${nHours}`}</em>
        </div>
        <div className="mt-kpi">
          <span>Payments {hour === null ? "on the map" : "this hour"}</span>
          <b>{window_.count.toLocaleString()}</b>
          <em>
            {money(window_.usd)} moved · {pct(window_.crossBorder)} cross-border
          </em>
        </div>
        <div className="mt-kpi">
          <span>
            {noun[0].toUpperCase() + noun.slice(1)}s on the map
          </span>
          <b>{onMap.length.toLocaleString()}</b>
          <em>
            {persons} people · {onMap.length - persons} companies · {model.nodes.filter((n) => n.kind === "context").length} outside the consortium
          </em>
        </div>
        <div className="mt-kpi mt-kpi--flag" style={{ ["--tier" as string]: tierColor(mode, flagTier) }}>
          <span>
            {TIER_GLYPHS[flagTier]} {extras.tiers[flagTier].label}
            {flagTier < extras.tiers.length - 1 ? " or above" : ""}
          </span>
          <b>{flaggedOnMap.toLocaleString()}</b>
          <em>
            on the map · {stats.flagged.toLocaleString()} of {stats.n.toLocaleString()} overall
          </em>
        </div>
        <div className={`mt-kpi mt-reveal${revealed ? " mt-reveal--on" : ""}`}>
          <label className="mt-switch">
            <input type="checkbox" checked={revealed} onChange={(e) => setRevealed(e.target.checked)} />
            <span className="mt-switch-track" />
            Reveal what happened
          </label>
          {revealed ? (
            <em>
              <b>{stats.positives}</b> {extras.outcome_label.toLowerCase()} · <b>{stats.caught}</b> flagged ({stats.positives ? pct(stats.caught / stats.positives) : "—"}) · precision{" "}
              {stats.flagged ? pct(stats.caught / stats.flagged) : "—"}
            </em>
          ) : (
            <em>
              Judge the model first — then see who was really involved.{" "}
              {loaded.crossFitted ? "Every score is cross-fitted: no holder was scored by a model that saw it." : "In-sample scores: cross-fitted ones are not trained yet."}
            </em>
          )}
        </div>
      </div>

      <div className="mt-main">
        <section className="mt-stage-wrap">
          <div className="mt-stage" ref={stageBox}>
            <canvas ref={canvas} aria-label={`World map of ${noun}s and their payments, coloured by risk`} />
            <div className="mt-legend">
              <div className="mt-legend-row">
                {extras.tiers.map((t, i) => (
                  <span key={t.label}>
                    <i style={{ background: tierColor(mode, i) }} /> {TIER_GLYPHS[i]} {t.label}
                  </span>
                ))}
              </div>
              <div className="mt-legend-row">
                <span>
                  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                    <circle cx="6" cy="6" r="4.2" fill="currentColor" />
                  </svg>{" "}
                  person
                </span>
                <span>
                  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                    <polygon points="6,1 11,6 6,11 1,6" fill="currentColor" />
                  </svg>{" "}
                  company
                </span>
                <span>
                  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                    <rect x="1.5" y="1.5" width="9" height="9" fill={colors.bank} />
                  </svg>{" "}
                  member bank
                </span>
                <span>
                  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                    <circle cx="6" cy="6" r="3.6" fill="none" stroke={colors.context} strokeWidth="1.2" />
                  </svg>{" "}
                  outside the consortium
                </span>
              </div>
              <div className="mt-legend-row">
                {extras.flows.map((f) => (
                  <span key={f.kind}>
                    <FormatMark kind={f.kind} /> {f.label}
                  </span>
                ))}
              </div>
              {selected && (
                <div className="mt-legend-row">
                  <span>
                    <i className="mt-legend-line" style={{ background: colors.out }} /> paid by the {noun} →
                  </span>
                  <span>
                    <i className="mt-legend-line" style={{ background: colors.in }} /> received →
                  </span>
                </div>
              )}
              {revealed && (
                <div className="mt-legend-row">
                  <span>
                    <i className="mt-legend-ring" /> involved, flagged
                  </span>
                  <span>
                    <i className="mt-legend-ring mt-legend-ring--missed" /> involved, missed
                  </span>
                </div>
              )}
            </div>
            {typology && revealed && (
              <button type="button" className="mt-filter-chip" onClick={() => setTypology(null)}>
                {typology} ✕
              </button>
            )}
            {hover && (
              <div className="mt-tip" style={{ left: hover.x, top: hover.y }}>
                <b>{hover.node.label}</b>
                <span>
                  {hover.node.kind === "entity"
                    ? `Member bank · ${hover.node.country}`
                    : hover.node.kind === "context"
                      ? `Outside the consortium · ${hover.node.country}`
                      : `${hover.node.detail || hover.node.country}`}
                </span>
                {hover.node.holder && (
                  <span>
                    {TIER_GLYPHS[hover.node.holder.tier]} {extras.tiers[hover.node.holder.tier].label} · {pct(hover.node.holder.probability, 1)} risk
                    {revealed ? ` · ${hover.node.holder.actual ? (hover.node.holder.typology ?? extras.outcome_label) : "not involved"}` : ""}
                  </span>
                )}
                {hover.node.kind === "row" && <span className="mt-tip-hint">click to follow its money</span>}
              </div>
            )}
          </div>
          <p className="mt-note">
            A schematic map: each country's holders cluster around its bank, nudged apart so Europe stays legible.{" "}
            {hour === null ? "Showing the ten days' biggest flows — press play to replay hour by hour." : "Click a holder to draw every payment it sent and received."}
          </p>

          <div className="mt-timeline">
            <div className="mt-timeline-controls">
              <button type="button" className="mt-play" onClick={togglePlay} aria-label={playing ? "Pause replay" : "Play replay"}>
                {playing ? "❚❚" : "▶"}
              </button>
              <button
                type="button"
                className={`mt-all${hour === null ? " mt-all--on" : ""}`}
                onClick={() => {
                  setPlaying(false);
                  setHour(null);
                }}
              >
                All ten days
              </button>
              <div className="mt-speed" role="group" aria-label="Replay speed">
                {SPEEDS.map((s) => (
                  <button key={s} type="button" className={s === speed ? "mt-speed--on" : ""} onClick={() => setSpeed(s)}>
                    {s}×
                  </button>
                ))}
              </div>
            </div>
            <div
              className="mt-bars"
              onClick={(e) => {
                const rect = e.currentTarget.getBoundingClientRect();
                const i = Math.min(nHours - 1, Math.max(0, Math.floor(((e.clientX - rect.left) / rect.width) * nHours)));
                setPlaying(false);
                setHour(i);
              }}
              role="slider"
              aria-label="Replay hour"
              aria-valuemin={0}
              aria-valuemax={nHours - 1}
              aria-valuenow={hour ?? undefined}
              aria-valuetext={hour === null ? "all ten days" : hourLabel(model.periods[hour])}
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight") setHour((h) => Math.min(nHours - 1, (h ?? -1) + 1));
                if (e.key === "ArrowLeft") setHour((h) => Math.max(0, (h ?? 1) - 1));
              }}
            >
              <svg viewBox={`0 0 ${nHours * 10} 60`} preserveAspectRatio="none">
                {Array.from(model.hourlyUsd, (usd, i) => {
                  const h = (Math.sqrt(usd) / Math.sqrt(maxHour)) * 54;
                  return (
                    <g key={i} opacity={hour === null || hour === i ? 1 : 0.5}>
                      <title>
                        {hourLabel(model.periods[i])}: {model.hourlyCount[i]} payments, {money(usd)}
                      </title>
                      <rect x={i * 10} y={0} width={10} height={60} fill="transparent" />
                      {h > 0 && <rect x={i * 10 + 1} y={58 - h} width={8} height={Math.max(0.5, h)} fill={colors.money} opacity={0.7} />}
                    </g>
                  );
                })}
                {days.map((d) => (
                  <line key={d} x1={d * 240} x2={d * 240} y1={0} y2={60} stroke={colors.dim} strokeWidth={1} opacity={0.35} vectorEffect="non-scaling-stroke" />
                ))}
              </svg>
              {hour !== null && <span className="mt-playhead" style={{ left: `${((hour + 0.5) / nHours) * 100}%` }} />}
              <div className="mt-days">
                {days.map((d) => (
                  <span key={d} style={{ left: `${((d * 24) / nHours) * 100}%` }}>
                    {dayLabel(model.periods[d * 24])}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </section>

        <aside className="mt-side">
          <section className="mt-ledger" aria-label="Ledger">
            <div className="mt-card-head">
              <span className="mt-card-title">LEDGER</span>
              <span className="mt-card-sub">{hour === null ? "biggest of the ten days" : clock}</span>
            </div>
            <div className="mt-ledger-rows">
              {window_.flows.slice(0, LEDGER_ROWS).map(({ flow, usd }, i) => (
                <div key={`${hour ?? "all"}-${flow.index}`} className="mt-ledger-row" style={{ animationDelay: `${Math.min(i, 12) * 26}ms` }}>
                  <span className="mt-ledger-fmt" title={formatLabel.get(flow.kind) ?? flow.kind}>
                    <FormatMark kind={flow.kind} />
                  </span>
                  <span className="mt-ledger-who">
                    <button type="button" onClick={() => flow.source.kind === "row" && setSelectedId(flow.source.id)} title={flow.source.label}>
                      {flow.source.holder ? <i style={{ color: tierColor("dark", flow.source.holder.tier) }}>{TIER_GLYPHS[flow.source.holder.tier]}</i> : <i>○</i>} {flow.source.label}
                    </button>
                    <span>→</span>
                    <button type="button" onClick={() => flow.target.kind === "row" && setSelectedId(flow.target.id)} title={flow.target.label}>
                      {flow.target.holder ? <i style={{ color: tierColor("dark", flow.target.holder.tier) }}>{TIER_GLYPHS[flow.target.holder.tier]}</i> : <i>○</i>} {flow.target.label}
                    </button>
                  </span>
                  <span className="mt-ledger-usd">
                    {money(usd)}
                    <em>{flow.currency ?? ""}</em>
                  </span>
                </div>
              ))}
              {window_.flows.length === 0 && <p className="mt-card-empty">No payments in this hour on the map.</p>}
            </div>
            <p className="mt-card-foot">
              Showing {Math.min(LEDGER_ROWS, window_.flows.length)} of {window_.flows.length.toLocaleString()} · click a name to follow its money
            </p>
          </section>

          <section className="mt-watch" aria-label="Watchlist">
            <div className="mt-card-head">
              <span className="mt-card-title">WATCHLIST</span>
              <span className="mt-card-sub">riskiest {noun}s on the map</span>
            </div>
            <div className="mt-watch-rows">
              {watchlist.map((n, i) => (
                <button
                  type="button"
                  key={n.id}
                  className={`mt-watch-row${selectedId === n.id ? " mt-watch-row--on" : ""}`}
                  style={{ animationDelay: `${Math.min(i, 12) * 24}ms`, ["--tier" as string]: tierColor("dark", n.holder!.tier) }}
                  onClick={() => setSelectedId(selectedId === n.id ? null : n.id)}
                >
                  <span className="mt-watch-tier" title={extras.tiers[n.holder!.tier].label}>
                    {TIER_GLYPHS[n.holder!.tier]}
                  </span>
                  <span className="mt-watch-name">
                    <b title={n.label}>{n.label}</b>
                    <em>{n.detail || n.country}</em>
                  </span>
                  <span className="mt-watch-risk">
                    {pct(n.holder!.probability)}
                    {revealed && <em className={n.holder!.actual ? "mt-bad" : "mt-ok"}>{n.holder!.actual ? (n.holder!.typology ?? "INVOLVED").toUpperCase() : "CLEAR"}</em>}
                  </span>
                </button>
              ))}
            </div>
            <p className="mt-card-foot">Click a row to draw its flows and open the dossier</p>
          </section>
        </aside>
      </div>

      {selected && selectedHolder && (
        <section className="mt-dossier" style={{ ["--tier" as string]: tierColor(mode, selectedHolder.tier) }}>
          <div className="mt-dossier-id">
            <span className="mt-badge">
              {TIER_GLYPHS[selectedHolder.tier]} {extras.tiers[selectedHolder.tier].label}
            </span>
            <h4>{selected.label}</h4>
            <p>
              {selected.person ? "Person" : "Company"} · {selected.detail || selected.country}
            </p>
            <p className="mt-dim">
              {(model.banksOf.get(selected.id) ?? []).map((b) => b.label).join(", ") || "—"} · #{selected.id}
            </p>
            <div className="mt-hero">
              <span className="mt-hero-value">{pct(selectedHolder.probability, 1)}</span>
              <span className="mt-hero-label">
                {extras.outcome_label.toLowerCase()}
                <br />
                {loaded.crossFitted ? "scored blind — by a model that never saw it" : "deployed model, in-sample"}
              </span>
            </div>
            {extras.tiers[selectedHolder.tier].description && <p className="mt-action">{extras.tiers[selectedHolder.tier].description}</p>}
            {revealed && (
              <p className={`mt-outcome${selectedHolder.actual ? " mt-outcome--bad" : ""}`}>
                {selectedHolder.actual
                  ? `✖ ${extras.outcome_label}${selectedHolder.typology ? ` — ${selectedHolder.typology}${selectedHolder.caseId ? ` (${selectedHolder.caseId})` : ""}` : ""}`
                  : `✓ ${scenario.target_value_labels?.["0"] ?? "Not involved"}`}
              </p>
            )}
            {revealed && selectedHolder.caseId && (
              <p className="mt-dim mt-small">{(model.caseMembers.get(selectedHolder.caseId) ?? []).length} holders of this scheme are on the map — outlined in gold.</p>
            )}
            <button type="button" className="mt-close" onClick={() => setSelectedId(null)}>
              Close
            </button>
          </div>

          <div className="mt-dossier-why">
            <h5>Why — pushes on this {noun}'s risk, by pillar</h5>
            {detailError && <p className="mt-dim">{detailError}</p>}
            {!detail && !detailError && <p className="mt-dim">Explaining…</p>}
            {detail &&
              pillars.map((p) => {
                const w = (Math.abs(p.value) / Math.max(1e-6, ...pillars.map((q) => Math.abs(q.value)))) * 50;
                const up = p.value >= 0;
                return (
                  <div key={p.label} className="mt-driver">
                    <span className="mt-driver-name">{p.label}</span>
                    <span className="mt-driver-track">
                      <span className="mt-driver-axis" />
                      <span
                        className="mt-driver-bar"
                        style={{ width: `${w}%`, left: up ? "50%" : `${50 - w}%`, background: up ? RISK_RAMP[mode].up : RISK_RAMP[mode].down, borderRadius: up ? "0 4px 4px 0" : "4px 0 0 4px" }}
                      />
                    </span>
                    <span className="mt-driver-value" style={{ color: up ? RISK_RAMP[mode].up : RISK_RAMP[mode].down }}>
                      {points(p.value)}
                    </span>
                  </div>
                );
              })}
            {detail && (
              <>
                <h5 className="mt-sub">Biggest single features</h5>
                {detail.items.slice(0, 5).map((item) => {
                  const up = item.value >= 0;
                  return (
                    <div key={item.feature} className="mt-feature">
                      <span>
                        {featureLabel(scenario, item.feature)}
                        <em>{featureValue(selectedHolder.record[item.feature])}</em>
                      </span>
                      <b style={{ color: up ? RISK_RAMP[mode].up : RISK_RAMP[mode].down }}>{points(item.value)}</b>
                    </div>
                  );
                })}
                <p className="mt-dim mt-small">
                  <span style={{ color: RISK_RAMP[mode].up }}>■</span> raises the risk <span style={{ color: RISK_RAMP[mode].down }}>■</span> lowers it · base rate {pct(detail.base, 1)}
                </p>
              </>
            )}
          </div>

          <div className="mt-dossier-facts">
            <h5>How it compares — percentile among all {stats.n.toLocaleString()} {noun}s</h5>
            {extras.facts.map((f) => {
              const value = Number(selectedHolder.record[f]);
              const p = percentileIn(factSorted.get(f) ?? [], value);
              return (
                <div key={f} className="mt-fact">
                  <span className="mt-fact-name">
                    {featureLabel(scenario, f)}
                    <em>{featureValue(value)}</em>
                  </span>
                  <span className="mt-fact-track" title={`${p.toFixed(0)}th percentile`}>
                    <span className="mt-fact-fill" style={{ width: `${p}%` }} />
                    <span className="mt-fact-dot" style={{ left: `${p}%` }} />
                  </span>
                  <span className="mt-fact-pct">{p.toFixed(0)}</span>
                </div>
              );
            })}
          </div>

          <div className="mt-dossier-flows">
            <h5>Money — {selectedFlows.length} counterparties on the map</h5>
            {selectedSeries && (
              <div className="mt-strip" title="Dollars per hour: sent above the line, received below">
                <svg viewBox={`0 0 ${nHours * 4} 44`} preserveAspectRatio="none">
                  {(() => {
                    const peak = Math.max(1, ...selectedSeries.out, ...selectedSeries.in);
                    return Array.from({ length: nHours }, (_, i) => {
                      const up = (Math.sqrt(selectedSeries.out[i]) / Math.sqrt(peak)) * 20;
                      const down = (Math.sqrt(selectedSeries.in[i]) / Math.sqrt(peak)) * 20;
                      return (
                        <g key={i}>
                          {up > 0 && <rect x={i * 4} y={22 - up} width={3} height={up} fill={colors.out} />}
                          {down > 0 && <rect x={i * 4} y={22} width={3} height={down} fill={colors.in} />}
                        </g>
                      );
                    });
                  })()}
                  <line x1={0} x2={nHours * 4} y1={22} y2={22} stroke={colors.dim} strokeWidth={1} opacity={0.5} vectorEffect="non-scaling-stroke" />
                  {hour !== null && <rect x={hour * 4 - 0.5} y={0} width={4} height={44} fill={colors.ink} opacity={0.18} />}
                </svg>
              </div>
            )}
            <div className="mt-flows">
              {selectedFlows.slice(0, 8).map((c) => (
                <button
                  type="button"
                  key={`${c.direction}:${c.node.id}`}
                  className="mt-flow"
                  disabled={c.node.kind !== "row"}
                  onClick={() => c.node.kind === "row" && setSelectedId(c.node.id)}
                >
                  <span className="mt-flow-dir" style={{ color: c.direction === "out" ? colors.out : colors.in }} title={c.direction === "out" ? "paid" : "received from"}>
                    {c.direction === "out" ? "→" : "←"}
                  </span>
                  <span className="mt-flow-name">
                    <b title={c.node.label}>{c.node.label}</b>
                    <em>{c.node.detail || c.node.country}</em>
                  </span>
                  <span className="mt-flow-kinds">
                    {[...c.kinds].map((k) => (
                      <FormatMark key={k} kind={k} />
                    ))}
                  </span>
                  <span className="mt-flow-usd">{money(c.usd)}</span>
                </button>
              ))}
              {selectedFlows.length === 0 && <p className="mt-dim">No payments on the map for this {noun}.</p>}
            </div>
          </div>
        </section>
      )}

      {revealed ? (
        <section className="mt-gallery" aria-label="Laundering typologies">
          <h5>
            The schemes behind the {stats.positives} — and how many the model flags{" "}
            <em>
              ({extras.tiers[flagTier].label}
              {flagTier < extras.tiers.length - 1 ? " or above" : ""})
            </em>
          </h5>
          <div className="mt-gallery-grid">
            {[...typologies]
              .sort((a, b) => Number(a.name === "Unpatterned") - Number(b.name === "Unpatterned") || b.holders - a.holders)
              .map((t) => {
                const share = t.holders ? t.caught / t.holders : 0;
                return (
                  <button
                    type="button"
                    key={t.name}
                    className={`mt-typo${typology === t.name ? " mt-typo--on" : ""}`}
                    onClick={() => setTypology((cur) => (cur === t.name ? null : t.name))}
                    title="Click to light this scheme up on the map"
                  >
                    <Motif name={t.name} color={typology === t.name ? colors.scheme : colors.dim} />
                    <b>{t.name}</b>
                    <span className="mt-typo-n">
                      {t.holders} {noun}s{t.attempts ? ` · ${t.attempts} attempts` : " · outside the 8 shapes"}
                    </span>
                    <span className="mt-typo-bar">
                      <span style={{ width: `${share * 100}%`, background: tierColor(mode, flagTier) }} />
                    </span>
                    <span className="mt-typo-share">{pct(share)} flagged</span>
                  </button>
                );
              })}
          </div>
        </section>
      ) : (
        <p className="mt-gallery-hint">Reveal what happened to see the laundering schemes behind the map — fan-outs, stacks, cycles — and how many of each the model flags.</p>
      )}

      {extras.disclaimer && <p className="mt-disclaimer">{extras.disclaimer}</p>}
      </div>
    </div>
  );
}
