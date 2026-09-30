import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import {
  datasetSample,
  modelCard,
  networkGraph,
  outOfFoldScores,
  predict,
  type ModelCard,
  type NetworkExplorerExtra,
  type ScenarioSummary,
} from "./apiClient";
import { config } from "./config";
import {
  backtest,
  buildNetwork,
  compact,
  formatPush,
  linkBetween,
  type Network,
  type NxNode,
  pct,
  percentileOf,
  plural,
  periodLabel,
  periodOf,
  relations,
  type RowInput,
  searchNodes,
  shapScale,
  type ShapScale,
  shortestPath,
  tierOf,
  topTies,
} from "./networkModel";
import { NetworkScene, type SceneMode } from "./networkScene";
import { explainByFeature, featureLabel, type Record_ } from "./predictUtils";
import { NETWORK_PALETTE, RISK_RAMP, surfaceMode, TIER_GLYPHS, tierColor } from "./riskPalette";
import { useTheme } from "./useTheme";
import "./networkExplorer.css";

/**
 * Generic 5th workspace tab for a binary-classification tabular_ml scenario that ships
 * a network (`dataset.graph`; `ui_extras: {kind: network_explorer}`, see
 * scenario_schema.NetworkExplorerExtra) — e.g. `enron_fraud_network`. Every row is a
 * glowing node coloured by its risk tier and scored *out of fold* (by a model that
 * never saw that row's label — prediction's /model/{slug}/out-of-fold), next to the
 * graph's curated entities and unscored context nodes. Pick a node for its dossier
 * (SHAP by pillar, peer percentiles, strongest ties, cited relations), trace the
 * shortest path between two nodes, replay the network month by month with its
 * events, colour it by community — then reveal the real outcome as a backtest. The
 * wording fields are the only domain vocabulary here; the stage is networkScene.ts.
 */

export type ScoreSource = "out_of_fold" | "deployed";

export type Loaded = {
  net: Network;
  card: ModelCard | null;
  source: ScoreSource;
  folds: number | null;
  oofAuc: number | null;
  sorted: Record<string, number[]>;
  truncated: boolean;
};

export type Detail = { base: number; items: { feature: string; value: number }[]; scale: ShapScale };

const CACHE = new Map<string, Promise<Loaded>>();
const MAX_CACHED = 8;

/** One load per scenario+caller, shared by the map and the Investigate view. */
export function loadNetworkCached(scenario: ScenarioSummary, extras: NetworkExplorerExtra, accessToken: string | null): { key: string; promise: Promise<Loaded>; drop: () => void } {
  const key = `${scenario.slug}|${accessToken ?? ""}`;
  if (!CACHE.has(key)) {
    if (CACHE.size >= MAX_CACHED) CACHE.delete(CACHE.keys().next().value!);
    CACHE.set(key, loadNetwork(scenario, extras, accessToken));
  }
  return { key, promise: CACHE.get(key)!, drop: () => CACHE.delete(key) };
}

async function loadNetwork(scenario: ScenarioSummary, extras: NetworkExplorerExtra, accessToken: string | null): Promise<Loaded> {
  const features = scenario.feature_columns ?? [];
  const url = config.predictionUrl;
  const [sample, graph, card, oof] = await Promise.all([
    datasetSample(url, scenario.slug, 30000, accessToken),
    networkGraph(url, scenario.slug, accessToken),
    modelCard(url, scenario.slug, accessToken).catch(() => null),
    outOfFoldScores(url, scenario.slug, accessToken).catch(() => null),
  ]);
  const records: Record_[] = sample.rows.map((row) => Object.fromEntries(features.map((f) => [f, row[f] as number | string])));
  const ids = sample.rows.map((row, i) => (sample.id_column ? String(row[sample.id_column]) : String(i + 1)));
  const oofById = new Map((oof?.rows ?? []).map((r) => [r.id, r]));
  const useOof = !!oof && ids.every((id) => oofById.has(id));
  let probabilities: number[];
  if (useOof) {
    probabilities = ids.map((id) => oofById.get(id)!.probability);
  } else {
    const response = await predict(url, scenario.slug, records, accessToken, { explain: false });
    probabilities = response.predictions.map((p) => p.prediction);
  }
  const target = scenario.target ?? "";
  const rows: RowInput[] = sample.rows.map((row, i) => ({
    id: ids[i],
    name: String(row[extras.name_column] ?? ids[i]),
    detail: extras.detail_columns.map((c) => row[c]).filter((v) => v !== null && v !== "").join(" · "),
    info: {
      record: records[i],
      display: row,
      probability: probabilities[i],
      tier: tierOf(probabilities[i], extras.tiers),
      actual: Number(row[target]) === 1 ? 1 : 0,
      size: extras.size_feature ? Number(row[extras.size_feature]) || 0 : 0,
      contributions: useOof ? oofById.get(ids[i])!.contributions : null,
    },
  }));
  const net = buildNetwork(graph, rows, extras);
  const sorted: Record<string, number[]> = {};
  for (const f of features) {
    if (scenario.feature_schema?.[f]?.type !== "numeric") continue;
    sorted[f] = records.map((r) => Number(r[f])).filter(Number.isFinite).sort((a, b) => a - b);
  }
  return {
    net,
    card,
    source: useOof ? "out_of_fold" : "deployed",
    folds: useOof ? oof!.folds : null,
    oofAuc: useOof ? oof!.roc_auc : null,
    sorted,
    truncated: sample.total_rows > sample.rows.length,
  };
}

function usePrefersReducedMotion(): boolean {
  const query = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const [reduced, setReduced] = useState(query?.matches ?? false);
  useEffect(() => {
    if (!query) return;
    const onChange = () => setReduced(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [query]);
  return reduced;
}

function logit(p: number): number {
  const q = Math.min(1 - 1e-6, Math.max(1e-6, p));
  return Math.log(q / (1 - q));
}

export function explain(features: string[], probability: number, contributions: Record<string, number>, scale: ShapScale): Detail {
  const byFeature = explainByFeature(features, probability, contributions);
  const sum = byFeature.items.reduce((s, i) => s + i.value, 0);
  const base = scale === "probability" ? byFeature.base : 1 / (1 + Math.exp(-(logit(probability) - sum)));
  return { base, items: byFeature.items, scale };
}

export function Track({ p, marker, markerTitle }: { p: number; marker?: number | null; markerTitle?: string }) {
  return (
    <span className="nx-track" aria-hidden>
      {marker !== null && marker !== undefined && <span className="nx-track-marker" style={{ left: `${marker * 100}%` }} title={markerTitle} />}
      <span className="nx-track-dot" style={{ left: `${p * 100}%` }} />
    </span>
  );
}

export function NetworkExplorerView({ scenario, accessToken, onInvestigate }: { scenario: ScenarioSummary; accessToken: string | null; onInvestigate?: (id: string) => void }) {
  const extras = scenario.ui_extras as NetworkExplorerExtra;
  const { theme } = useTheme();
  const surface = surfaceMode(theme.cssVars["--bg"]);
  const ramp = RISK_RAMP[surface];
  const palette = NETWORK_PALETTE[surface];
  const reducedMotion = usePrefersReducedMotion();
  const noun = extras.entity_noun;
  const features = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [mode, setMode] = useState<SceneMode>("risk");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [pathFrom, setPathFrom] = useState<string | null>(null);
  const [pathEnds, setPathEnds] = useState<{ from: string; to: string } | null>(null);
  const [viaContext, setViaContext] = useState(true);
  const [period, setPeriod] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [revealAt, setRevealAt] = useState<number | null>(null);
  const [tierFilter, setTierFilter] = useState<ReadonlySet<number>>(new Set());
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [banner, setBanner] = useState<{ key: number; date: string; label: string; description?: string | null } | null>(null);

  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<NetworkScene | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const pickRef = useRef<string | null>(null);
  pickRef.current = pathFrom;
  const selectRef = useRef<(id: string | null) => void>(() => undefined);

  const flagTier = useMemo(() => {
    const i = extras.tiers.findIndex((t) => t.label === extras.flag_from_tier);
    return i >= 0 ? i : extras.tiers.length - 1;
  }, [extras.tiers, extras.flag_from_tier]);
  const revealed = revealAt !== null;
  const net = loaded?.net ?? null;
  const scale = shapScale(loaded?.card?.model_name);
  const path = useMemo(() => {
    if (!net || !pathEnds) return [];
    return (shortestPath(net, pathEnds.from, pathEnds.to, viaContext) ?? []).map((n) => n.id);
  }, [net, pathEnds, viaContext]);

  // ── data ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    const { promise, drop } = loadNetworkCached(scenario, extras, accessToken);
    let cancelled = false;
    promise
      .then((data) => !cancelled && setLoaded(data))
      .catch((e: Error) => {
        drop();
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [scenario, extras, accessToken]);

  const ranked = useMemo(() => (net ? [...net.rows].sort((a, b) => (b.row?.probability ?? 0) - (a.row?.probability ?? 0)) : []), [net]);
  const pinned = useMemo(() => new Set(ranked.slice(0, 10).map((n) => n.id)), [ranked]);
  const tierCounts = useMemo(() => {
    const counts = extras.tiers.map(() => ({ n: 0, positive: 0 }));
    for (const node of ranked) {
      counts[node.row!.tier].n += 1;
      counts[node.row!.tier].positive += node.row!.actual;
    }
    return counts;
  }, [ranked, extras.tiers]);
  const test = useMemo(() => (net ? backtest(net.rows, flagTier) : null), [net, flagTier]);
  const totalFlow = useMemo(() => (net ? net.periodTotals.reduce((s, v) => s + v, 0) : 0), [net]);
  const entityCount = useMemo(() => (net ? net.nodes.filter((n) => n.kind === "entity").length : 0), [net]);

  // ── the stage ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!net || !canvasRef.current || !stageRef.current) return;
    const scene = new NetworkScene(canvasRef.current, {
      onHover: (id, x, y) => setHover(id ? { id, x, y } : null),
      onSelect: (id) => selectRef.current(id),
      onReady: () => setReady(true),
    });
    sceneRef.current = scene;
    const stage = stageRef.current;
    scene.resize(stage.clientWidth, stage.clientHeight);
    scene.setNetwork(net);
    const observer = new ResizeObserver(() => scene.resize(stage.clientWidth, stage.clientHeight));
    observer.observe(stage);
    return () => {
      observer.disconnect();
      scene.destroy();
      sceneRef.current = null;
    };
  }, [net]);

  useEffect(() => {
    sceneRef.current?.setView({
      mode,
      surface,
      selectedId,
      hoveredId: hover?.id ?? null,
      path,
      period,
      playing,
      revealAt,
      flagTier,
      tierFilter,
      reducedMotion,
      pinned,
    });
  }, [mode, surface, selectedId, hover, path, period, playing, revealAt, flagTier, tierFilter, reducedMotion, pinned, net]);

  // Selecting: in "pick a destination" mode it completes the path; otherwise the camera
  // eases onto the node and its neighbours.
  const select = useCallback(
    (id: string | null) => {
      if (!id) {
        if (!pickRef.current) setSelectedId(null);
        return;
      }
      setSelectedId(id);
      if (!net) return;
      if (pickRef.current && pickRef.current !== id) {
        setPathEnds({ from: pickRef.current, to: id });
        setPathFrom(null);
        return;
      }
      const around = [id, ...(net.adjacency.get(id) ?? []).map((x) => x.node.id)];
      sceneRef.current?.focusOn(around);
    },
    [net],
  );
  selectRef.current = select;

  useEffect(() => {
    if (path.length) sceneRef.current?.focusOn(path);
  }, [path]);

  const clearPath = useCallback(() => {
    setPathEnds(null);
    setPathFrom(null);
  }, []);

  // ── explanation of the selected row ─────────────────────────────────────
  const selected = selectedId && net ? (net.byId.get(selectedId) ?? null) : null;
  useEffect(() => {
    setDetail(null);
    setDetailError(null);
    const row = selected?.row;
    if (!row) return;
    if (row.contributions) {
      setDetail(explain(features, row.probability, row.contributions, scale));
      return;
    }
    let cancelled = false;
    predict(config.predictionUrl, scenario.slug, [row.record], accessToken)
      .then((response) => {
        const p = response.predictions[0];
        if (!cancelled) setDetail(explain(features, p.prediction, p.contributions, scale));
      })
      .catch((e: Error) => !cancelled && setDetailError(e.message));
    return () => {
      cancelled = true;
    };
  }, [selected, features, scale, scenario.slug, accessToken]);

  const pillarBars = useMemo(() => {
    if (!detail) return [];
    const byFeature = new Map(detail.items.map((i) => [i.feature, i.value]));
    const inPillar = new Set(extras.pillars.flatMap((p) => p.features));
    const bars = extras.pillars.map((p) => ({ label: p.label, value: p.features.reduce((s, f) => s + (byFeature.get(f) ?? 0), 0) }));
    const rest = detail.items.filter((i) => !inPillar.has(i.feature)).reduce((s, i) => s + i.value, 0);
    if (Math.abs(rest) > 1e-6) bars.push({ label: "Other", value: rest });
    return bars;
  }, [detail, extras.pillars]);
  const pillarMax = Math.max(1e-6, ...pillarBars.map((b) => Math.abs(b.value)));

  const positiveMedian = useCallback(
    (feature: string): number | null => {
      if (!revealed || !net) return null;
      const values = net.rows
        .filter((n) => n.row?.actual === 1)
        .map((n) => Number(n.row!.record[feature]))
        .filter(Number.isFinite)
        .sort((a, b) => a - b);
      if (!values.length) return null;
      return percentileOf(loaded?.sorted[feature] ?? [], values[Math.floor(values.length / 2)]);
    },
    [revealed, net, loaded],
  );

  // ── replay ───────────────────────────────────────────────────────────────
  const periods = useMemo(() => net?.periods ?? [], [net]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(
      () => {
        setPeriod((p) => {
          const next = p === null ? 0 : p + 1;
          if (next >= periods.length) {
            setPlaying(false);
            return periods.length - 1;
          }
          return next;
        });
      },
      reducedMotion ? 1100 : 720,
    );
    return () => window.clearInterval(timer);
  }, [playing, periods.length, reducedMotion]);

  useEffect(() => {
    if (!playing || period === null) return;
    const event = extras.events.find((e) => periodOf(periods, e.date) === period);
    if (!event) return;
    setBanner({ key: period, ...event });
    const timer = window.setTimeout(() => setBanner(null), 2600);
    return () => window.clearTimeout(timer);
  }, [playing, period, periods, extras.events]);

  const togglePlay = useCallback(() => {
    if (playing) {
      setPlaying(false);
      return;
    }
    setPeriod((p) => (p === null || p >= periods.length - 1 ? 0 : p));
    setPlaying(true);
  }, [playing, periods.length]);

  // ── search ───────────────────────────────────────────────────────────────
  const results = useMemo(() => (net && query ? searchNodes(net, query) : []), [net, query]);
  const pick = useCallback(
    (node: NxNode) => {
      setQuery("");
      setSearchOpen(false);
      select(node.id);
    },
    [select],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = (event.target as HTMLElement)?.tagName === "INPUT";
      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
      } else if (event.key === "Escape") {
        if (pathFrom || path.length) clearPath();
        else setSelectedId(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pathFrom, path.length, clearPath]);

  function toggleTier(tier: number) {
    setTierFilter((prev) => {
      const next = new Set(prev);
      if (next.has(tier)) next.delete(tier);
      else next.add(tier);
      return next;
    });
  }

  // ── chat context ─────────────────────────────────────────────────────────
  const ties = useMemo(() => (net && selected ? topTies(net, selected.id) : []), [net, selected]);
  const rels = useMemo(() => (net && selected ? relations(net, selected.id) : []), [net, selected]);
  useCopilotReadable({
    description: `${scenario.title} network tab: every ${noun} scored (${loaded?.source === "out_of_fold" ? "out of fold — by a model that never saw that " + noun + "'s own label" : "by the deployed model"}), the network's entities with their cited relations, and what the user is looking at. A score is resemblance to a profile, never evidence of wrongdoing: follow the scenario's guardrails when discussing named people.`,
    value: net
      ? {
          score_source: loaded?.source,
          tiers: extras.tiers.map((t, i) => ({ tier: t.label, from_probability: t.min_probability, count: tierCounts[i].n })),
          top_10: ranked.slice(0, 10).map((n) => ({ id: n.id, name: n.label, role: n.detail, score: Number(n.row!.probability.toFixed(3)), tier: extras.tiers[n.row!.tier].label })),
          colour_mode: mode,
          replay_month: period !== null ? periods[period] : "all",
          selected: selected
            ? {
                id: selected.id,
                name: selected.label,
                kind: selected.kind,
                role_or_type: selected.detail || selected.type,
                description: selected.description,
                score: selected.row ? Number(selected.row.probability.toFixed(3)) : undefined,
                tier: selected.row ? extras.tiers[selected.row.tier].label : undefined,
                pillars: pillarBars.map((b) => ({ pillar: b.label, push: formatPush(b.value, scale) })),
                top_drivers: detail?.items.slice(0, 6).map((d) => ({ feature: featureLabel(scenario, d.feature), value: selected.row?.record[d.feature], push: formatPush(d.value, scale) })),
                strongest_ties: ties.map((t) => ({ with: t.node.kind === "context" ? extras.context_label : t.node.label, [extras.link_noun]: t.total })),
                relations: rels.map((r) => ({ with: r.node.label, relation: r.label, source: r.citation })),
                ...(revealed && selected.row ? { public_record: selected.row.actual ? extras.outcome_label : "not on the list" } : {}),
              }
            : null,
          traced_path: path.map((id) => net.byId.get(id)?.label),
          outcomes_revealed: revealed,
          backtest: revealed && test ? { flagged_from_tier: extras.tiers[flagTier].label, ...test } : undefined,
        }
      : null,
  });

  if (error) return <div className="nx-empty">Could not load the network: {error}</div>;
  if (!loaded || !net) {
    return (
      <div className="nx-empty nx-loading">
        <span className="nx-spinner" /> Building the network — scoring every {noun}…
      </div>
    );
  }

  const cvAuc = loaded.card?.metrics.cv_roc_auc_mean ?? null;
  const cvStd = loaded.card?.metrics.cv_roc_auc_std ?? null;
  const holdoutAuc = loaded.card?.metrics.holdout_roc_auc ?? null;
  const hoverNode = hover ? net.byId.get(hover.id) : null;
  const q = query.trim();

  return (
    <div className={`nx nx--${surface}`} data-nx-ready={ready ? "true" : "false"}>
      <header className="nx-head">
        <div>
          <h3>{extras.title}</h3>
          {extras.subtitle && <p>{extras.subtitle}</p>}
        </div>
        <div className="nx-kpis">
          <span className="nx-kpi">
            <b>{net.rows.length}</b> {plural(noun)}
          </span>
          <span className="nx-kpi" title={`${extras.link_noun} between the people drawn on the map, over the whole period`}>
            <b>{compact(totalFlow)}</b> {extras.link_noun} mapped
          </span>
          <span className="nx-kpi">
            <b>{entityCount}</b> {plural(extras.entity_label)}
          </span>
          {cvAuc !== null && (
            <span className="nx-kpi" title="Cross-validated ROC AUC of the selected model family on the training split (1.0 = perfect ordering, 0.5 = chance).">
              ROC AUC <b>{cvAuc.toFixed(2)}</b>
              {cvStd !== null && <em> ± {cvStd.toFixed(2)}</em>} <em>cv</em>
            </span>
          )}
          {holdoutAuc !== null && (
            <span className="nx-kpi" title={`Untouched hold-out of ${loaded.card?.holdout_rows ?? "?"} rows — with so few positives it swings a lot; trust the cross-validated figure.`}>
              hold-out <b>{holdoutAuc.toFixed(2)}</b>
            </span>
          )}
        </div>
      </header>

      <div className="nx-toolbar">
        <div className="nx-seg" role="radiogroup" aria-label="Colour by">
          {(
            [
              ["risk", "Model score"],
              ["communities", extras.group_label + " "],
            ] as [SceneMode, string][]
          ).map(([m, label]) => (
            <button key={m} type="button" role="radio" aria-checked={mode === m} className={mode === m ? "nx-seg--on" : ""} onClick={() => setMode(m)}>
              {label.trim()}
            </button>
          ))}
        </div>

        <div className="nx-search">
          <input
            ref={searchRef}
            type="search"
            role="combobox"
            aria-expanded={searchOpen && results.length > 0}
            aria-controls="nx-search-list"
            aria-autocomplete="list"
            placeholder={pathFrom ? `Pick the destination — search a ${noun} or ${extras.entity_label}…` : `Search a ${noun}, id or ${extras.entity_label}…  ( / )`}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSearchOpen(true);
            }}
            onFocus={() => setSearchOpen(true)}
            onBlur={() => window.setTimeout(() => setSearchOpen(false), 150)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && results[0]) pick(results[0]);
            }}
          />
          {searchOpen && q && (
            <ul id="nx-search-list" className="nx-search-list" role="listbox">
              {results.map((node) => (
                <li key={node.id} role="option" aria-selected={false} onMouseDown={() => pick(node)}>
                  <span className="nx-glyph" style={{ color: node.row ? tierColor(surface, node.row.tier) : palette.entity }}>
                    {node.row ? TIER_GLYPHS[node.row.tier] : "⬢"}
                  </span>
                  <span className="nx-search-name">{node.label}</span>
                  <span className="nx-search-detail">{node.kind === "row" ? `${node.detail} · ${node.id}` : node.detail}</span>
                  {node.row && <span className="nx-search-score">{pct(node.row.probability)}</span>}
                </li>
              ))}
              {!results.length && <li className="nx-search-none">No match</li>}
            </ul>
          )}
        </div>

        <label className="nx-switch" title="Show the real outcome as rings: solid = flagged, dashed = missed">
          <input type="checkbox" checked={revealed} onChange={(e) => setRevealAt(e.target.checked ? performance.now() : null)} />
          <span className="nx-switch-track" />
          Reveal the public record
        </label>

        <div className="nx-zoom" role="group" aria-label="Zoom">
          <button type="button" onClick={() => sceneRef.current?.zoomBy(1.4)} aria-label="Zoom in">
            +
          </button>
          <button type="button" onClick={() => sceneRef.current?.zoomBy(1 / 1.4)} aria-label="Zoom out">
            −
          </button>
          <button type="button" onClick={() => sceneRef.current?.fit()} aria-label="Fit the whole network">
            ⤢
          </button>
        </div>
      </div>

      <div className="nx-tiers" role="group" aria-label="Filter by tier">
        {extras.tiers.map((tier, t) => {
          const active = tierFilter.has(t);
          const next = extras.tiers[t + 1]?.min_probability;
          return (
            <button
              key={tier.label}
              type="button"
              className={`nx-tier${active ? " nx-tier--on" : ""}${tierFilter.size && !active ? " nx-tier--dim" : ""}`}
              style={{ ["--tier" as string]: tierColor(surface, t) }}
              aria-pressed={active}
              title={tier.description ?? tier.label}
              onClick={() => toggleTier(t)}
            >
              <span className="nx-tier-head">
                <span className="nx-glyph">{TIER_GLYPHS[t]}</span> {tier.label}
              </span>
              <span className="nx-tier-count">{tierCounts[t].n}</span>
              <span className="nx-tier-range">{next !== undefined ? `${pct(tier.min_probability)}–${pct(next)}` : `≥ ${pct(tier.min_probability)}`}</span>
              {revealed && <span className="nx-tier-pos">◯ {tierCounts[t].positive} on the record</span>}
            </button>
          );
        })}
        {revealed && test ? (
          <div className="nx-backtest">
            <b>{test.tp}</b> of <b>{test.positives}</b> persons on the public record scored <em>{extras.tiers[flagTier].label}</em> or above (solid rings);{" "}
            <b>{test.fn}</b> were missed (dashed). <b>{test.fp}</b> flagged {plural(noun)} were never on it — model false positives, not accusations. Precision{" "}
            <b>{test.flagged ? pct(test.tp / test.flagged) : "—"}</b> vs a base rate of {pct(test.positives / Math.max(1, net.rows.length))}
            {test.flagged && test.positives ? ` (${(test.tp / test.flagged / (test.positives / net.rows.length)).toFixed(1)}× lift)` : ""}.
            {loaded.source === "out_of_fold" && <span> Every score is out of fold: no {noun} was scored by a model that saw their own outcome.</span>}
          </div>
        ) : (
          <div className="nx-backtest nx-backtest--hint">
            Judge the model first — then reveal the public record ({extras.outcome_label.toLowerCase()}).
          </div>
        )}
      </div>

      <div className="nx-main">
        <div className="nx-stage" ref={stageRef}>
          <canvas ref={canvasRef} role="img" aria-label={`Network of ${net.rows.length} ${plural(noun)}, ${entityCount} ${plural(extras.entity_label)} and their ${extras.link_noun}. Use the search box or the ranked list below to explore it with the keyboard.`} />
          {!ready && (
            <div className="nx-stage-loading">
              <span className="nx-spinner" /> Laying out the network…
            </div>
          )}
          {hoverNode && hover && (
            <div
              className="nx-tooltip"
              style={{ left: hover.x > (stageRef.current?.clientWidth ?? 0) - 290 ? hover.x - 274 : hover.x + 14, top: hover.y + 14 }}
            >
              <b>{hoverNode.kind === "context" ? extras.context_label : hoverNode.label}</b>
              {hoverNode.kind === "row" && hoverNode.row && (
                <>
                  <span>
                    {hoverNode.detail} · {hoverNode.id}
                  </span>
                  <span>
                    <span style={{ color: tierColor(surface, hoverNode.row.tier) }}>{TIER_GLYPHS[hoverNode.row.tier]}</span> {extras.tiers[hoverNode.row.tier].label} · score {pct(hoverNode.row.probability)}
                  </span>
                  <span>
                    {hoverNode.degree} two-way contacts on the map
                    {revealed && hoverNode.row.actual ? ` · ◯ ${extras.outcome_label}` : ""}
                  </span>
                </>
              )}
              {hoverNode.kind === "entity" && <span>{hoverNode.type}</span>}
              {hoverNode.kind === "context" && <span>{hoverNode.degree} ties to scored {plural(noun)} — not in the dataset</span>}
            </div>
          )}
          {banner && (
            <div key={banner.key} className="nx-banner" role="status">
              <span className="nx-banner-date">{periodLabel(banner.date)}</span>
              <b>{banner.label}</b>
              {banner.description && <span>{banner.description}</span>}
            </div>
          )}
          {pathFrom && (
            <div className="nx-pick" role="status">
              Tracing from <b>{net.byId.get(pathFrom)?.label}</b> — click the destination or search it. <button type="button" onClick={clearPath}>Cancel</button>
            </div>
          )}
          <div className="nx-legend" aria-hidden>
            {mode === "risk" ? (
              <>
                <span>
                  <i className="nx-dot" style={{ background: ramp.tiers[2] }} /> score, by tier
                </span>
                <span>size = {extras.size_feature ? featureLabel(scenario, extras.size_feature).toLowerCase() : noun}</span>
              </>
            ) : (
              <>
                {net.groups
                  .filter((g) => g.colorIndex >= 0)
                  .map((g) => (
                    <span key={g.name}>
                      <i className="nx-dot" style={{ background: palette.communities[g.colorIndex] }} /> {g.name}
                    </span>
                  ))}
                {net.groups.some((g) => g.colorIndex < 0) && (
                  <span>
                    <i className="nx-dot" style={{ background: palette.other }} /> smaller {plural(extras.group_label.toLowerCase())} (outlined)
                  </span>
                )}
              </>
            )}
            <span>
              <i className="nx-hex" style={{ borderColor: mode === "communities" ? palette.dim : palette.entity }} /> {extras.entity_label}
            </span>
            <span>
              <i className="nx-dot nx-dot--small" style={{ background: palette.context }} /> {extras.context_label}
            </span>
            <span>
              <i className="nx-line" /> {extras.link_noun} <i className="nx-line nx-line--dash" style={{ borderColor: palette.entity }} /> relation
            </span>
            {revealed && (
              <span>
                ◯ on the record · <span className="nx-dashed-ring" /> missed
              </span>
            )}
          </div>
        </div>

        <aside className="nx-dossier" aria-live="polite">
          {pathEnds && !path.length && (
            <div className="nx-path">
              <div className="nx-path-head">
                <h5>No path</h5>
                <button type="button" onClick={clearPath} aria-label="Clear the path">
                  ✕
                </button>
              </div>
              <p className="nx-muted">
                {net.byId.get(pathEnds.from)?.label} and {net.byId.get(pathEnds.to)?.label} are not connected{viaContext ? "" : ` without ${plural(extras.context_label)}`}.
              </p>
              {!viaContext && (
                <label className="nx-check">
                  <input type="checkbox" checked={viaContext} onChange={(e) => setViaContext(e.target.checked)} /> through {plural(extras.context_label)}
                </label>
              )}
            </div>
          )}
          {path.length > 0 ? (
            <PathPanel net={net} path={path} extras={extras} surface={surface} viaContext={viaContext} setViaContext={setViaContext} onPick={select} onClear={clearPath} />
          ) : null}
          {selected ? (
            <Dossier
              key={selected.id}
              scenario={scenario}
              extras={extras}
              net={net}
              node={selected}
              surface={surface}
              detail={detail}
              detailError={detailError}
              pillarBars={pillarBars}
              pillarMax={pillarMax}
              ties={ties}
              rels={rels}
              sorted={loaded.sorted}
              revealed={revealed}
              source={loaded.source}
              folds={loaded.folds}
              positiveMedian={positiveMedian}
              onPick={select}
              onClose={() => setSelectedId(null)}
              onInvestigate={onInvestigate && selected.kind === "row" ? () => onInvestigate(selected.id) : undefined}
              onTrace={() => {
                setPathEnds(null);
                setPathFrom(selected.id);
                searchRef.current?.focus();
              }}
            />
          ) : (
            !path.length && (
              <div className="nx-board">
                <h4>Case board</h4>
                <p className="nx-muted">
                  Click a {noun} or a hexagon, search one, or press play on the timeline. Colour = how closely a profile resembles the {extras.outcome_label.toLowerCase()}{" "}
                  {loaded.source === "out_of_fold" ? `— every score out of fold (${loaded.folds}-fold cross-fitting)` : ""}.
                </p>
                <h5>Highest scores</h5>
                <ol className="nx-top">
                  {ranked.slice(0, 10).map((node) => (
                    <li key={node.id}>
                      <button type="button" onClick={() => select(node.id)}>
                        <span className="nx-glyph" style={{ color: tierColor(surface, node.row!.tier) }}>
                          {TIER_GLYPHS[node.row!.tier]}
                        </span>
                        <span className="nx-top-name">{node.label}</span>
                        <span className="nx-top-score">{pct(node.row!.probability)}</span>
                        {revealed && <span className="nx-top-outcome">{node.row!.actual ? "◯" : ""}</span>}
                      </button>
                    </li>
                  ))}
                </ol>
                <h5>{plural(extras.entity_label)[0].toUpperCase() + plural(extras.entity_label).slice(1)}</h5>
                <div className="nx-chips">
                  {net.nodes
                    .filter((n) => n.kind === "entity")
                    .map((n) => (
                      <button key={n.id} type="button" className="nx-chip" onClick={() => select(n.id)}>
                        <span style={{ color: palette.entity }}>⬢</span> {n.label}
                      </button>
                    ))}
                </div>
              </div>
            )
          )}
        </aside>
      </div>

      <Timeline
        net={net}
        extras={extras}
        period={period}
        playing={playing}
        onPeriod={(p) => {
          setPlaying(false);
          setPeriod(p);
        }}
        onTogglePlay={togglePlay}
      />

      {extras.disclaimer && <p className="nx-disclaimer">{extras.disclaimer}</p>}

      <details className="nx-table">
        <summary>
          All {net.rows.length} {plural(noun)}, ranked by score — the accessible list view
        </summary>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Id</th>
              <th>{noun[0].toUpperCase() + noun.slice(1)}</th>
              <th className="nx-num">Score</th>
              <th className="nx-num">Contacts</th>
              {revealed && <th>Record</th>}
            </tr>
          </thead>
          <tbody>
            {ranked
              .filter((n) => tierFilter.size === 0 || tierFilter.has(n.row!.tier))
              .map((node, rank) => (
                <tr key={node.id} className={node.id === selectedId ? "nx-row--on" : ""} tabIndex={0} onClick={() => select(node.id)} onKeyDown={(k) => k.key === "Enter" && select(node.id)}>
                  <td className="nx-num">{rank + 1}</td>
                  <td className="nx-id">{node.id}</td>
                  <td>
                    <span className="nx-glyph" style={{ color: tierColor(surface, node.row!.tier) }}>
                      {TIER_GLYPHS[node.row!.tier]}
                    </span>{" "}
                    {node.label} <span className="nx-muted">{node.detail}</span>
                  </td>
                  <td className="nx-num">{pct(node.row!.probability, 1)}</td>
                  <td className="nx-num">{node.degree}</td>
                  {revealed && <td>{node.row!.actual ? `◯ ${extras.outcome_label}` : ""}</td>}
                </tr>
              ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

// ── the dossier ────────────────────────────────────────────────────────────

function Dossier({
  scenario,
  extras,
  net,
  node,
  surface,
  detail,
  detailError,
  pillarBars,
  pillarMax,
  ties,
  rels,
  sorted,
  revealed,
  source,
  folds,
  positiveMedian,
  onPick,
  onClose,
  onTrace,
  onInvestigate,
}: {
  scenario: ScenarioSummary;
  extras: NetworkExplorerExtra;
  net: Network;
  node: NxNode;
  surface: "dark" | "light";
  detail: Detail | null;
  detailError: string | null;
  pillarBars: { label: string; value: number }[];
  pillarMax: number;
  ties: ReturnType<typeof topTies>;
  rels: ReturnType<typeof relations>;
  sorted: Record<string, number[]>;
  revealed: boolean;
  source: ScoreSource;
  folds: number | null;
  positiveMedian: (feature: string) => number | null;
  onPick: (id: string) => void;
  onClose: () => void;
  onTrace: () => void;
  onInvestigate?: () => void;
}) {
  const ramp = RISK_RAMP[surface];
  const palette = NETWORK_PALETTE[surface];
  const row = node.row;
  const scale = detail?.scale ?? "probability";

  const header = (
    <div className="nx-dossier-head">
      <span className="nx-kind">{node.kind === "row" ? extras.entity_noun : node.kind === "entity" ? node.type ?? extras.entity_label : extras.context_label}</span>
      <h4>{node.kind === "context" ? extras.context_label[0].toUpperCase() + extras.context_label.slice(1) : node.label}</h4>
      {node.kind === "row" && (
        <p className="nx-muted">
          {node.detail} · id <code>{node.id}</code>
        </p>
      )}
      <div className="nx-dossier-actions">
        {onInvestigate && (
          <button type="button" className="nx-investigate" onClick={onInvestigate}>
            Investigate ›
          </button>
        )}
        {node.kind !== "context" && (
          <button type="button" onClick={onTrace}>
            Trace a path from here
          </button>
        )}
        <button type="button" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
    </div>
  );

  const tieList = ties.length > 0 && (
    <section>
      <h5>Strongest ties</h5>
      <ul className="nx-ties">
        {ties.map((t) => (
          <li key={t.node.id}>
            <button type="button" onClick={() => onPick(t.node.id)}>
              <span className="nx-glyph" style={{ color: t.node.row ? tierColor(surface, t.node.row.tier) : palette.context }}>
                {t.node.row ? TIER_GLYPHS[t.node.row.tier] : "·"}
              </span>
              <span className="nx-tie-name">{t.node.kind === "context" ? extras.context_label : t.node.label}</span>
              <span className="nx-tie-count" title={`sent ${t.sent} · received ${t.received}`}>
                {t.total} {extras.link_noun}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );

  const relationList = rels.length > 0 && (
    <section>
      <h5>{node.kind === "entity" ? "Documented links" : "Documented relations"}</h5>
      <ul className="nx-rels">
        {rels.map((r, i) => (
          <li key={`${r.node.id}-${i}`}>
            <button type="button" onClick={() => onPick(r.node.id)}>
              <span style={{ color: r.node.kind === "entity" ? palette.entity : r.node.row ? tierColor(surface, r.node.row.tier) : palette.context }}>
                {r.node.kind === "entity" ? "⬢" : r.node.row ? TIER_GLYPHS[r.node.row.tier] : "·"}
              </span>{" "}
              <b>{r.node.label}</b>
            </button>
            <span className="nx-rel-label">
              {r.outgoing ? "" : "← "}
              {r.label}
            </span>
            {r.citation && <cite>{r.citation}</cite>}
          </li>
        ))}
      </ul>
    </section>
  );

  if (node.kind === "entity") {
    return (
      <div className="nx-dossier-body nx-dossier--entity" style={{ ["--tier" as string]: palette.entity }}>
        {header}
        {node.description && <p>{node.description}</p>}
        {relationList}
        {tieList}
      </div>
    );
  }
  if (node.kind === "context" || !row) {
    return (
      <div className="nx-dossier-body">
        {header}
        <p className="nx-muted">Not in the scored dataset — shown, anonymised, only because this person connects scored {plural(extras.entity_noun)} ({node.degree} two-way ties on the map).</p>
        {tieList}
      </div>
    );
  }

  const tier = extras.tiers[row.tier];
  const drivers = detail?.items.slice(0, 6) ?? [];
  return (
    <div className="nx-dossier-body" style={{ ["--tier" as string]: tierColor(surface, row.tier) }}>
      {header}
      <div className="nx-hero">
        <span className="nx-hero-value">{pct(row.probability)}</span>
        <span className="nx-hero-badge">
          {TIER_GLYPHS[row.tier]} {tier.label}
        </span>
      </div>
      <p className="nx-muted nx-hero-note">
        {source === "out_of_fold"
          ? `Scored out of fold: the ${folds}-fold model that produced this score never saw this ${extras.entity_noun}'s own outcome.`
          : `Scored by the deployed model, which saw this ${extras.entity_noun} in training (no out-of-fold scores available).`}
        {tier.description ? ` ${tier.description}` : ""}
      </p>
      {revealed && (
        <p className={`nx-outcome${row.actual ? " nx-outcome--on" : ""}`}>
          {row.actual ? `◯ ${extras.outcome_label}` : `Not on the public record (${scenario.target_value_labels?.["0"] ?? "negative"})`}
        </p>
      )}

      <section>
        <h5>What drives the score — by pillar</h5>
        {detailError && <p className="nx-muted">{detailError}</p>}
        {!detail && !detailError && <p className="nx-muted">Explaining…</p>}
        {detail && (
          <>
            <div className="nx-pillars">
              {pillarBars.map((bar) => {
                const w = (Math.abs(bar.value) / pillarMax) * 50;
                const up = bar.value >= 0;
                return (
                  <div key={bar.label} className="nx-pillar" title={`${bar.label}: ${formatPush(bar.value, scale)}`}>
                    <span className="nx-pillar-label">{bar.label}</span>
                    <span className="nx-pillar-track">
                      <span className="nx-pillar-axis" />
                      <span
                        className="nx-pillar-bar"
                        style={{ width: `${w}%`, left: up ? "50%" : `${50 - w}%`, background: up ? ramp.up : ramp.down, borderRadius: up ? "0 4px 4px 0" : "4px 0 0 4px" }}
                      />
                    </span>
                    <span className="nx-pillar-value">{formatPush(bar.value, scale)}</span>
                  </div>
                );
              })}
            </div>
            <p className="nx-key">
              <span style={{ color: ramp.up }}>■</span> towards the profile <span style={{ color: ramp.down }}>■</span> away from it · average score {pct(detail.base, 1)}
            </p>
          </>
        )}
      </section>

      {drivers.length > 0 && (
        <section>
          <h5>Top drivers — where this {extras.entity_noun} sits among all</h5>
          {drivers.map((d) => {
            const value = row.record[d.feature];
            const numeric = typeof value === "number" && sorted[d.feature];
            const p = numeric ? percentileOf(sorted[d.feature], value as number) : null;
            return (
              <div key={d.feature} className="nx-driver">
                <span className="nx-driver-name">{featureLabel(scenario, d.feature)}</span>
                <span className="nx-driver-value">{typeof value === "number" ? compact(value) : String(value)}</span>
                <span className="nx-driver-push" style={{ color: d.value >= 0 ? ramp.up : ramp.down }}>
                  {formatPush(d.value, scale)}
                </span>
                {p !== null && <Track p={p} marker={positiveMedian(d.feature)} markerTitle={`Median of the ${extras.outcome_label.toLowerCase()}`} />}
              </div>
            );
          })}
          <p className="nx-key">Track = percentile among all {plural(extras.entity_noun)}{revealed ? " · ◆ median of those on the public record" : ""}</p>
        </section>
      )}

      {extras.facts.length > 0 && (
        <section>
          <h5>Profile facts</h5>
          <div className="nx-facts">
            {extras.facts.map((f) => {
              const value = Number(row.record[f]);
              const p = sorted[f] ? percentileOf(sorted[f], value) : 0;
              return (
                <div key={f} className="nx-fact">
                  <span className="nx-fact-name">{featureLabel(scenario, f)}</span>
                  <span className="nx-fact-value">{compact(value)}</span>
                  <Track p={p} />
                  <span className="nx-fact-pct">p{Math.round(p * 100)}</span>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {relationList}
      {tieList}
      {!ties.length && !rels.length && <p className="nx-muted">No two-way {extras.link_noun} tie on the map.</p>}
      <p className="nx-muted nx-small">
        {net.adjacency.get(node.id)?.length ?? 0} links · {node.degree} two-way contacts
      </p>
    </div>
  );
}

// ── the traced path ────────────────────────────────────────────────────────

function PathPanel({
  net,
  path,
  extras,
  surface,
  viaContext,
  setViaContext,
  onPick,
  onClear,
}: {
  net: Network;
  path: string[];
  extras: NetworkExplorerExtra;
  surface: "dark" | "light";
  viaContext: boolean;
  setViaContext: (v: boolean) => void;
  onPick: (id: string) => void;
  onClear: () => void;
}) {
  const palette = NETWORK_PALETTE[surface];
  const nodes = path.map((id) => net.byId.get(id)!);
  return (
    <div className="nx-path">
      <div className="nx-path-head">
        <h5>
          Path · {nodes.length - 1} hop{nodes.length === 2 ? "" : "s"}
        </h5>
        <button type="button" onClick={onClear} aria-label="Clear the path">
          ✕
        </button>
      </div>
      <label className="nx-check">
        <input type="checkbox" checked={viaContext} onChange={(e) => setViaContext(e.target.checked)} /> through {plural(extras.context_label)}
      </label>
      <ol>
        {nodes.map((node, i) => {
          const link = i > 0 ? linkBetween(net, nodes[i - 1].id, node.id) : null;
          let how = "";
          if (link?.flow) {
            const back = linkBetween(net, node.id, nodes[i - 1].id);
            const both = (net.adjacency.get(nodes[i - 1].id) ?? []).filter((x) => x.node.id === node.id && x.link.flow).reduce((s, x) => s + x.link.weight, 0);
            how = `${both || back?.weight || link.weight} ${extras.link_noun}`;
          } else if (link) {
            how = link.label ?? link.kind;
          }
          return (
            <li key={`${node.id}-${i}`}>
              {i > 0 && (
                <span className="nx-hop" title={link?.citation}>
                  ↓ {how}
                  {link?.citation && <cite>{link.citation}</cite>}
                </span>
              )}
              <button type="button" onClick={() => onPick(node.id)}>
                <span className="nx-hop-n">{i + 1}</span>
                <span style={{ color: node.kind === "entity" ? palette.entity : node.row ? tierColor(surface, node.row.tier) : palette.context }}>
                  {node.kind === "entity" ? "⬢" : node.row ? TIER_GLYPHS[node.row.tier] : "·"}
                </span>{" "}
                {node.kind === "context" ? extras.context_label : node.label}
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ── the timeline ───────────────────────────────────────────────────────────

export function Timeline({
  net,
  extras,
  period,
  playing,
  onPeriod,
  onTogglePlay,
  totals,
  overlay,
  caption,
}: {
  net: Network;
  extras: NetworkExplorerExtra;
  period: number | null;
  playing: boolean;
  onPeriod: (p: number | null) => void;
  onTogglePlay: () => void;
  // The Investigate view plots one subject's own monthly volume (and, over it, the
  // part exchanged with targets) instead of the whole network's.
  totals?: ArrayLike<number>;
  overlay?: ArrayLike<number>;
  caption?: string;
}) {
  const n = net.periods.length;
  const series = Array.from(totals ?? net.periodTotals);
  const [openEvent, setOpenEvent] = useState<number | null>(null);
  if (n < 2) return null;
  const max = Math.max(1, ...series);
  const x = (i: number) => ((i + 0.5) / n) * 1000;
  const y = (v: number) => 70 - (v / max) * 62;
  const area = `M ${x(0)} 70 ` + series.map((v, i) => `L ${x(i)} ${y(v)}`).join(" ") + ` L ${x(n - 1)} 70 Z`;
  const line = series.map((v, i) => `${i ? "L" : "M"} ${x(i)} ${y(v)}`).join(" ");
  const hot = overlay ? `M ${x(0)} 70 ` + Array.from(overlay).map((v, i) => `L ${x(i)} ${y(v)}`).join(" ") + ` L ${x(n - 1)} 70 Z` : null;
  const years = net.periods.map((p, i) => [p, i] as const).filter(([p]) => p.endsWith("-01"));
  const current = period ?? n - 1;
  const events = extras.events.map((e, i) => ({ ...e, i, at: periodOf(net.periods, e.date) })).filter((e) => e.at >= 0);
  return (
    <div className="nx-timeline">
      <div className="nx-timeline-controls">
        <button type="button" className="nx-play" onClick={onTogglePlay} aria-label={playing ? "Pause the replay" : "Replay month by month"}>
          {playing ? "❚❚" : "▶"}
        </button>
        <div className="nx-timeline-now">
          <b>{period === null ? "All months" : periodLabel(net.periods[period])}</b>
          <span className="nx-muted">
            {period === null
              ? `${periodLabel(net.periods[0])} – ${periodLabel(net.periods[n - 1])} · ${caption ?? `${extras.link_noun} per month`}`
              : `${Math.round(series[period]).toLocaleString()} ${extras.link_noun} this month`}
          </span>
        </div>
        <button type="button" className={`nx-all${period === null ? " nx-all--on" : ""}`} onClick={() => onPeriod(null)}>
          All months
        </button>
      </div>
      <div className="nx-timeline-chart">
        <svg viewBox="0 0 1000 72" preserveAspectRatio="none" aria-hidden>
          <path d={area} className="nx-area" />
          {hot && <path d={hot} className="nx-area-hot" />}
          <path d={line} className="nx-area-line" vectorEffect="non-scaling-stroke" />
          {period !== null && <rect x={x(0) - 500 / n} y={0} width={x(current) + 500 / n - (x(0) - 500 / n)} height={72} className="nx-area-past" />}
          {period !== null && <line x1={x(current)} x2={x(current)} y1={0} y2={72} className="nx-playhead" vectorEffect="non-scaling-stroke" />}
        </svg>
        {events.map((e) => (
          <button
            key={e.i}
            type="button"
            className={`nx-pin${openEvent === e.i ? " nx-pin--open" : ""}${period !== null && e.at <= current ? " nx-pin--past" : ""}`}
            style={{ left: `${((e.at + 0.5) / n) * 100}%` }}
            onMouseEnter={() => setOpenEvent(e.i)}
            onMouseLeave={() => setOpenEvent(null)}
            onFocus={() => setOpenEvent(e.i)}
            onBlur={() => setOpenEvent(null)}
            onClick={() => onPeriod(e.at)}
            aria-label={`${periodLabel(e.date)}: ${e.label}`}
          >
            <span className="nx-pin-dot" />
            {openEvent === e.i && (
              <span className="nx-pin-tip" role="tooltip">
                <span className="nx-muted">{periodLabel(e.date)}</span>
                <b>{e.label}</b>
                {e.description && <span>{e.description}</span>}
              </span>
            )}
          </button>
        ))}
        <input
          className="nx-scrub"
          type="range"
          min={0}
          max={n - 1}
          step={1}
          value={current}
          aria-label="Replay month"
          aria-valuetext={periodLabel(net.periods[current])}
          onChange={(e) => onPeriod(Number(e.target.value))}
        />
      </div>
      <div className="nx-years" aria-hidden>
        {years.map(([p, i]) => (
          <span key={p} style={{ left: `${((i + 0.5) / n) * 100}%` }}>
            {p.slice(0, 4)}
          </span>
        ))}
      </div>
      <div className="nx-events">
        {events.map((e) => (
          <button key={e.i} type="button" className={period === e.at ? "nx-event--on" : ""} onClick={() => onPeriod(e.at)} title={e.description ?? e.label}>
            <span className="nx-muted">{periodLabel(e.date)}</span> {e.label}
          </button>
        ))}
      </div>
    </div>
  );
}
