import { useCallback, useEffect, useMemo, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { datasetSample, modelCard, predict, type RiskWatchlistExtra, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { PlotlyChart } from "./PlotlyChart";
import type { PlotlyDatum, PlotlyLayout } from "./plotly";
import { explainByFeature, featureLabel, type Record_ } from "./predictUtils";
import { RISK_RAMP, surfaceMode, TIER_GLYPHS } from "./riskPalette";
import { useTheme } from "./useTheme";
import "./riskWatchlist.css";

/**
 * Generic 5th workspace tab for a binary-classification tabular_ml scenario whose
 * rows are entities under supervision and whose positive class is the adverse
 * outcome (`ui_extras: {kind: risk_watchlist}`, see scenario_schema.RiskWatchlistExtra)
 * — e.g. `bank_early_warning`. Every row is scored in one batched /predict call
 * (probabilities only), sorted into the scenario's supervisory tiers, and shown three
 * ways at once: a map, a two-ratio "risk landscape", and a ranked watchlist. Picking
 * an entity explains it on demand (SHAP, rolled up into the scenario's pillars) next
 * to where each of its ratios sits among its peers; the real outcome can be revealed
 * as a backtest. The wording fields are the only domain vocabulary here.
 *
 * Colour: tiers are ordered, so they use a one-hue ordinal ramp (red, stepped per
 * light/dark surface — validated with the dataviz palette validator, `--ordinal`), with
 * the lowest tier recessive grey (emphasis: ~90% of entities are the context, not the
 * story). A tier never relies on colour alone — every mark and row also carries its
 * glyph and label. Pillar bars use the blue↔red diverging pair.
 */

const RAMP = RISK_RAMP;
const GLYPHS = TIER_GLYPHS;
const TABLE_PAGE = 120;

type Entity = {
  index: number;
  id: string;
  name: string;
  detail: string;
  lat: number | null;
  lon: number | null;
  size: number;
  x: number;
  y: number;
  record: Record_;
  probability: number;
  tier: number;
  actual: 0 | 1;
};

type Loaded = { entities: Entity[]; cvAuc: number | null; holdoutAuc: number | null; truncated: boolean };
type Detail = { base: number; items: { feature: string; value: number }[] };

// One scored watchlist per scenario + caller, for the session (a re-visit is instant).
const CACHE = new Map<string, Promise<Loaded>>();

function tierOf(probability: number, extras: RiskWatchlistExtra): number {
  let tier = 0;
  extras.tiers.forEach((t, i) => {
    if (probability >= t.min_probability) tier = i;
  });
  return tier;
}

async function loadWatchlist(scenario: ScenarioSummary, extras: RiskWatchlistExtra, accessToken: string | null): Promise<Loaded> {
  const features = scenario.feature_columns ?? [];
  const cardPromise = modelCard(config.predictionUrl, scenario.slug, accessToken).catch(() => null);
  const sample = await datasetSample(config.predictionUrl, scenario.slug, 30000, accessToken);
  const records = sample.rows.map((row) => Object.fromEntries(features.map((f) => [f, row[f] as number | string])));
  const response = await predict(config.predictionUrl, scenario.slug, records, accessToken, { explain: false });
  const target = scenario.target ?? "";
  const entities = sample.rows.map((row, index): Entity => {
    const probability = response.predictions[index].prediction;
    const num = (column: string | null | undefined) => (column ? Number(row[column]) : NaN);
    return {
      index,
      id: sample.id_column ? String(row[sample.id_column]) : String(index + 1),
      name: String(row[extras.name_column] ?? ""),
      detail: extras.detail_columns.map((c) => row[c]).filter((v) => v !== null && v !== "").join(", "),
      lat: extras.map ? num(extras.map.lat_column) : null,
      lon: extras.map ? num(extras.map.lon_column) : null,
      size: extras.size_feature ? num(extras.size_feature) : 1,
      x: num(extras.landscape_x),
      y: num(extras.landscape_y),
      record: records[index],
      probability,
      tier: tierOf(probability, extras),
      actual: Number(row[target]) === 1 ? 1 : 0,
    };
  });
  const card = await cardPromise;
  return {
    entities,
    cvAuc: card?.metrics.cv_roc_auc_mean ?? null,
    holdoutAuc: card?.metrics.holdout_roc_auc ?? null,
    truncated: sample.total_rows > sample.rows.length,
  };
}

function pct(p: number, digits = 1): string {
  return `${(p * 100).toFixed(digits)}%`;
}

function points(v: number): string {
  return `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pts`;
}

function compact(v: number): string {
  if (!Number.isFinite(v)) return "—";
  if (Math.abs(v) >= 1000) return `${(v / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })}k`;
  return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function markerSize(size: number): number {
  return 4 + 3.1 * Math.log10(1 + Math.max(0, size));
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return NaN;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

/** Share of `sorted` strictly below `v` — where an entity's ratio sits among its peers. */
function percentileOf(sorted: number[], v: number): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] < v) lo = mid + 1;
    else hi = mid;
  }
  return sorted.length ? lo / sorted.length : 0;
}

export function RiskWatchlistView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as RiskWatchlistExtra;
  const { theme } = useTheme();
  const bg = theme.cssVars["--bg"] ?? "#05070f";
  const mode = surfaceMode(bg);
  const colors = RAMP[mode];
  const tierColor = useCallback((tier: number) => (tier === 0 ? colors.base : colors.tiers[Math.min(tier - 1, 2)]), [colors]);
  const noun = extras.entity_noun;
  const numericFeatures = useMemo(
    () => (scenario.feature_columns ?? []).filter((f) => scenario.feature_schema?.[f]?.type === "numeric"),
    [scenario.feature_columns, scenario.feature_schema],
  );

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"map" | "landscape">(extras.map ? "map" : "landscape");
  const [tierFilter, setTierFilter] = useState<ReadonlySet<number>>(new Set());
  const [query, setQuery] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [selected, setSelected] = useState<Entity | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [tableRows, setTableRows] = useState(TABLE_PAGE);

  useEffect(() => {
    const key = `${scenario.slug}|${accessToken ?? ""}`;
    if (!CACHE.has(key)) CACHE.set(key, loadWatchlist(scenario, extras, accessToken));
    let cancelled = false;
    CACHE.get(key)!
      .then((data) => !cancelled && setLoaded(data))
      .catch((e: Error) => {
        CACHE.delete(key);
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [scenario, extras, accessToken]);

  const entities = useMemo(() => loaded?.entities ?? [], [loaded]);

  // Peer distributions (every entity), and the failed entities' medians as a reference mark.
  const peers = useMemo(() => {
    const sorted: Record<string, number[]> = {};
    const failedMedian: Record<string, number> = {};
    for (const f of numericFeatures) {
      const all = entities.map((e) => Number(e.record[f])).filter(Number.isFinite);
      sorted[f] = [...all].sort((a, b) => a - b);
      const failed = entities.filter((e) => e.actual === 1).map((e) => Number(e.record[f])).filter(Number.isFinite).sort((a, b) => a - b);
      failedMedian[f] = quantile(failed, 0.5);
    }
    return { sorted, failedMedian };
  }, [entities, numericFeatures]);

  const tierCounts = useMemo(() => {
    const counts = extras.tiers.map(() => ({ n: 0, failed: 0 }));
    for (const e of entities) {
      counts[e.tier].n += 1;
      counts[e.tier].failed += e.actual;
    }
    return counts;
  }, [entities, extras.tiers]);
  const totalFailed = tierCounts.reduce((s, c) => s + c.failed, 0);
  const interventionFrom = Math.min(2, extras.tiers.length - 1);
  const flagged = tierCounts.slice(interventionFrom).reduce((s, c) => s + c.failed, 0);
  const flaggedCount = tierCounts.slice(interventionFrom).reduce((s, c) => s + c.n, 0);

  const q = query.trim().toLowerCase();
  const visible = useMemo(
    () =>
      entities.filter(
        (e) => (tierFilter.size === 0 || tierFilter.has(e.tier)) && (!q || e.name.toLowerCase().includes(q) || e.detail.toLowerCase().includes(q) || e.id === q),
      ),
    [entities, tierFilter, q],
  );
  const ranked = useMemo(() => [...visible].sort((a, b) => b.probability - a.probability), [visible]);

  useEffect(() => setTableRows(TABLE_PAGE), [tierFilter, q]);

  const select = useCallback(
    (entity: Entity) => {
      setSelected(entity);
      setDetail(null);
      setDetailError(null);
      predict(config.predictionUrl, scenario.slug, [entity.record], accessToken)
        .then((response) => {
          const p = response.predictions[0];
          setDetail(explainByFeature(scenario.feature_columns ?? [], p.prediction, p.contributions));
        })
        .catch((e: Error) => setDetailError(e.message));
    },
    [scenario.slug, scenario.feature_columns, accessToken],
  );

  function toggleTier(tier: number) {
    setTierFilter((prev) => {
      const next = new Set(prev);
      if (next.has(tier)) next.delete(tier);
      else next.add(tier);
      return next;
    });
  }

  // ── chart traces: one per tier (bottom tier drawn first, recessive) ──────────
  const hover = useCallback(
    (e: Entity) =>
      `<b>${e.name}</b><br>${e.detail}<br>${GLYPHS[e.tier] ?? "●"} ${extras.tiers[e.tier].label} · ${pct(e.probability)}` +
      (revealed ? `<br>${e.actual ? `✖ ${extras.outcome_label}` : "✓ " + (scenario.target_value_labels?.["0"] ?? "No")}` : ""),
    [extras, revealed, scenario.target_value_labels],
  );

  const traces = useCallback(
    (geo: boolean): PlotlyDatum[] => {
      const type = geo ? "scattergeo" : "scattergl";
      const out: PlotlyDatum[] = extras.tiers.map((tier, t) => {
        const members = visible.filter((e) => e.tier === t && (!geo || (e.lat !== null && Number.isFinite(e.lat))));
        const coords = geo
          ? { lat: members.map((e) => e.lat), lon: members.map((e) => e.lon) }
          : { x: members.map((e) => e.x), y: members.map((e) => e.y) };
        return {
          type,
          mode: "markers",
          name: `${GLYPHS[t]} ${tier.label}`,
          ...coords,
          customdata: members.map((e) => e.index),
          text: members.map(hover),
          hovertemplate: "%{text}<extra></extra>",
          marker: {
            size: members.map((e) => markerSize(e.size) * (t === 0 ? 0.7 : 1)),
            color: tierColor(t),
            opacity: t === 0 ? 0.45 : 0.9,
            line: { width: t === 0 ? 0 : 1, color: bg },
          },
        };
      });
      if (revealed) {
        const failed = visible.filter((e) => e.actual === 1 && (!geo || (e.lat !== null && Number.isFinite(e.lat))));
        out.push({
          type,
          mode: "markers",
          name: `◯ ${extras.outcome_label}`,
          ...(geo ? { lat: failed.map((e) => e.lat), lon: failed.map((e) => e.lon) } : { x: failed.map((e) => e.x), y: failed.map((e) => e.y) }),
          customdata: failed.map((e) => e.index),
          text: failed.map(hover),
          hovertemplate: "%{text}<extra></extra>",
          marker: { size: failed.map((e) => markerSize(e.size) + 6), symbol: "circle-open", color: colors.ring, line: { width: 1.6 } },
        });
      }
      if (selected) {
        out.push({
          type,
          mode: "markers",
          name: selected.name,
          showlegend: false,
          ...(geo ? { lat: [selected.lat], lon: [selected.lon] } : { x: [selected.x], y: [selected.y] }),
          customdata: [selected.index],
          text: [hover(selected)],
          hovertemplate: "%{text}<extra></extra>",
          marker: { size: markerSize(selected.size) + 14, symbol: "circle-open", color: theme.cssVars["--accent"] ?? "#33c7ff", line: { width: 2.5 } },
        });
      }
      return out;
    },
    [extras, visible, hover, tierColor, bg, revealed, colors.ring, selected, theme.cssVars],
  );

  const onPoint = useCallback(
    (_i: number, raw: unknown) => {
      const index = (raw as { points?: { customdata?: number }[] })?.points?.[0]?.customdata;
      if (index !== undefined && entities[index]) select(entities[index]);
    },
    [entities, select],
  );

  const legend = { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { size: 11 }, bgcolor: "rgba(0,0,0,0)" };
  const mapLayout: PlotlyLayout = {
    paper_bgcolor: "rgba(0,0,0,0)",
    margin: { t: 30, r: 0, b: 0, l: 0 },
    showlegend: true,
    legend,
    geo: {
      scope: extras.map?.scope ?? "world",
      projection: extras.map?.scope === "usa" ? { type: "albers usa" } : undefined,
      showland: true,
      landcolor: theme.cssVars["--panel-2"] ?? "#101b36",
      showlakes: true,
      lakecolor: theme.cssVars["--panel"] ?? "#0b1224",
      showsubunits: true,
      subunitcolor: theme.cssVars["--border"] ?? "#1c3a5e",
      showcountries: true,
      countrycolor: theme.cssVars["--border-strong"] ?? "#2c5a8a",
      showcoastlines: true,
      coastlinecolor: theme.cssVars["--border-strong"] ?? "#2c5a8a",
      bgcolor: "rgba(0,0,0,0)",
    },
  };
  const range = (feature: string) => {
    const sorted = peers.sorted[feature] ?? [];
    // Keep the low tail (the weakest entities are the story), trim only extreme outliers.
    const lo = quantile(sorted, 0.001);
    const hi = quantile(sorted, 0.995);
    const pad = (hi - lo) * 0.04;
    return [lo - pad, hi + pad];
  };
  const landscapeLayout: PlotlyLayout = {
    margin: { t: 30, r: 12, b: 48, l: 56 },
    showlegend: true,
    legend,
    hovermode: "closest",
    xaxis: { title: { text: featureLabel(scenario, extras.landscape_x) }, range: range(extras.landscape_x), zeroline: false },
    yaxis: { title: { text: featureLabel(scenario, extras.landscape_y) }, range: range(extras.landscape_y), zeroline: false },
  };

  // ── the selected entity's profile ───────────────────────────────────────────
  const pillarBars = useMemo(() => {
    if (!detail) return [];
    const byFeature = new Map(detail.items.map((i) => [i.feature, i.value]));
    const inPillar = new Set(extras.pillars.flatMap((p) => p.features));
    const bars = extras.pillars.map((p) => ({ label: p.label, value: p.features.reduce((s, f) => s + (byFeature.get(f) ?? 0), 0) }));
    const rest = detail.items.filter((i) => !inPillar.has(i.feature)).reduce((s, i) => s + i.value, 0);
    if (Math.abs(rest) > 1e-6) bars.push({ label: "Profile", value: rest });
    return bars;
  }, [detail, extras.pillars]);
  const pillarMax = Math.max(1e-6, ...pillarBars.map((b) => Math.abs(b.value)));
  const drivers = useMemo(() => (detail ? detail.items.slice(0, 7) : []), [detail]);

  useCopilotReadable({
    description: `${scenario.title} watchlist: every ${noun} scored and sorted into supervisory tiers, plus the ${noun} the user is inspecting (if any). Use it to answer which ${noun}s are highest-risk, how many sit in each tier, or why the selected ${noun} is where it is.`,
    value: loaded
      ? {
          tiers: extras.tiers.map((t, i) => ({ tier: t.label, from_probability: t.min_probability, count: tierCounts[i].n })),
          top_10: ranked.slice(0, 10).map((e) => ({ name: e.name, where: e.detail, probability: Number(e.probability.toFixed(3)), tier: extras.tiers[e.tier].label })),
          selected: selected
            ? {
                name: selected.name,
                where: selected.detail,
                probability: Number(selected.probability.toFixed(3)),
                tier: extras.tiers[selected.tier].label,
                pillar_contributions: pillarBars.map((b) => ({ pillar: b.label, points: Number((b.value * 100).toFixed(1)) })),
                ...(revealed ? { actual_outcome: selected.actual ? extras.outcome_label : "survived" } : {}),
              }
            : null,
          outcomes_revealed: revealed,
        }
      : null,
  });

  if (error) return <div className="rw-empty">Could not load the watchlist: {error}</div>;
  if (!loaded) {
    return (
      <div className="rw-empty rw-loading">
        <span className="rw-spinner" /> Scoring every {noun}…
      </div>
    );
  }

  const selectedTier = selected ? extras.tiers[selected.tier] : null;
  return (
    <div className={`rw rw--${mode}`}>
      <header className="rw-head">
        <div>
          <h3>{extras.title}</h3>
          {extras.subtitle && <p>{extras.subtitle}</p>}
        </div>
        <div className="rw-model">
          {loaded.cvAuc !== null && (
            <span title="Out-of-sample ranking quality: cross-validated ROC AUC of the deployed model family (1.0 = perfect ordering, 0.5 = chance).">
              ROC AUC <b>{loaded.cvAuc.toFixed(3)}</b> <em>cross-validated</em>
            </span>
          )}
          {loaded.holdoutAuc !== null && (
            <span>
              hold-out <b>{loaded.holdoutAuc.toFixed(3)}</b>
            </span>
          )}
        </div>
      </header>

      <div className="rw-tiers" role="group" aria-label="Tiers">
        {extras.tiers.map((tier, t) => {
          const active = tierFilter.has(t);
          const next = extras.tiers[t + 1]?.min_probability;
          return (
            <button
              key={tier.label}
              type="button"
              className={`rw-tier${active ? " rw-tier--active" : ""}${tierFilter.size > 0 && !active ? " rw-tier--dim" : ""}`}
              style={{ ["--tier" as string]: tierColor(t) }}
              onClick={() => toggleTier(t)}
              aria-pressed={active}
              title={tier.description ?? tier.label}
            >
              <span className="rw-tier-head">
                <span className="rw-tier-glyph">{GLYPHS[t]}</span>
                {tier.label}
              </span>
              <span className="rw-tier-count">{tierCounts[t].n.toLocaleString()}</span>
              <span className="rw-tier-range">
                {next !== undefined ? `${pct(tier.min_probability, 0)}–${pct(next, 0)}` : `≥ ${pct(tier.min_probability, 0)}`} · {pct(tierCounts[t].n / entities.length)}
              </span>
              {revealed && (
                <span className="rw-tier-failed">
                  ✖ {tierCounts[t].failed} {extras.outcome_label.toLowerCase()} ({tierCounts[t].n ? pct(tierCounts[t].failed / tierCounts[t].n, 0) : "—"})
                </span>
              )}
            </button>
          );
        })}
        <div className={`rw-backtest${revealed ? " rw-backtest--on" : ""}`}>
          <label className="rw-switch">
            <input type="checkbox" checked={revealed} onChange={(e) => setRevealed(e.target.checked)} />
            <span className="rw-switch-track" />
            Reveal what happened
          </label>
          {revealed ? (
            <p>
              <b>{flagged}</b> of <b>{totalFailed}</b> {noun}s that went on to fail sat at <em>{extras.tiers[interventionFrom].label}</em> or above
              — {flaggedCount.toLocaleString()} {noun}s flagged ({pct(flaggedCount / entities.length)} of all). In-sample: the model saw these {noun}s
              in training; its honest ranking quality is the cross-validated AUC.
            </p>
          ) : (
            <p>Hide the outcome, judge the model — then reveal who actually failed.</p>
          )}
        </div>
      </div>

      <div className="rw-filters">
        {extras.map && (
          <div className="rw-seg" role="tablist">
            <button type="button" role="tab" aria-selected={view === "map"} className={view === "map" ? "rw-seg--on" : ""} onClick={() => setView("map")}>
              Map
            </button>
            <button type="button" role="tab" aria-selected={view === "landscape"} className={view === "landscape" ? "rw-seg--on" : ""} onClick={() => setView("landscape")}>
              Risk landscape
            </button>
          </div>
        )}
        <input className="rw-search" type="search" placeholder={`Search a ${noun}, city or id…`} value={query} onChange={(e) => setQuery(e.target.value)} />
        <span className="rw-shown">
          {visible.length.toLocaleString()} of {entities.length.toLocaleString()} {noun}s
          {tierFilter.size > 0 && (
            <button type="button" className="rw-clear" onClick={() => setTierFilter(new Set())}>
              clear tiers
            </button>
          )}
        </span>
      </div>

      <div className="rw-main">
        <div className="rw-chart">
          {view === "map" && extras.map ? (
            <PlotlyChart data={traces(true)} layout={mapLayout} height={560} onPointClick={onPoint} />
          ) : (
            <PlotlyChart data={traces(false)} layout={landscapeLayout} height={560} onPointClick={onPoint} />
          )}
          <p className="rw-chart-note">
            Dot size: {extras.size_feature ? featureLabel(scenario, extras.size_feature).toLowerCase() : noun} (log scale). Click a dot to inspect a {noun}.
          </p>
        </div>

        <div className="rw-list">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>{noun[0].toUpperCase() + noun.slice(1)}</th>
                <th className="rw-num">P(fail)</th>
                {revealed && <th />}
              </tr>
            </thead>
            <tbody>
              {ranked.slice(0, tableRows).map((e, rank) => (
                <tr
                  key={e.id}
                  className={selected?.index === e.index ? "rw-row--on" : ""}
                  onClick={() => select(e)}
                  tabIndex={0}
                  onKeyDown={(k) => k.key === "Enter" && select(e)}
                >
                  <td className="rw-rank">{rank + 1}</td>
                  <td>
                    <span className="rw-name">
                      <span className="rw-glyph" style={{ color: tierColor(e.tier) }} title={extras.tiers[e.tier].label}>
                        {GLYPHS[e.tier]}
                      </span>
                      {e.name}
                    </span>
                    <span className="rw-detail">{e.detail}</span>
                  </td>
                  <td className="rw-num">
                    <span className="rw-prob">
                      <span className="rw-prob-bar" style={{ width: `${Math.max(2, e.probability * 100)}%`, background: tierColor(e.tier) }} />
                      <span>{pct(e.probability)}</span>
                    </span>
                  </td>
                  {revealed && <td className="rw-outcome">{e.actual ? <span title={extras.outcome_label}>✖</span> : ""}</td>}
                </tr>
              ))}
            </tbody>
          </table>
          {ranked.length > tableRows && (
            <button type="button" className="rw-more" onClick={() => setTableRows((n) => n + TABLE_PAGE)}>
              Show {Math.min(TABLE_PAGE, ranked.length - tableRows)} more
            </button>
          )}
          {ranked.length === 0 && <p className="rw-none">No {noun} matches.</p>}
        </div>
      </div>

      {selected && selectedTier && (
        <section className="rw-profile" style={{ ["--tier" as string]: tierColor(selected.tier) }}>
          <div className="rw-profile-id">
            <span className="rw-profile-badge">
              {GLYPHS[selected.tier]} {selectedTier.label}
            </span>
            <h4>{selected.name}</h4>
            <p>
              {selected.detail} · id {selected.id}
              {extras.size_feature && ` · ${featureLabel(scenario, extras.size_feature)} ${compact(selected.size)}`}
            </p>
            <div className="rw-hero">
              <span className="rw-hero-value">{pct(selected.probability)}</span>
              <span className="rw-hero-label">{scenario.target_label ?? "Probability"}</span>
            </div>
            {selectedTier.description && <p className="rw-profile-action">{selectedTier.description}</p>}
            {revealed && (
              <p className={`rw-profile-outcome${selected.actual ? " rw-profile-outcome--bad" : ""}`}>
                {selected.actual ? `✖ ${extras.outcome_label}` : `✓ ${scenario.target_value_labels?.["0"] ?? "Survived"}`}
              </p>
            )}
            <button type="button" className="rw-close" onClick={() => setSelected(null)}>
              Close
            </button>
          </div>

          <div className="rw-profile-pillars">
            <h5>What drives the score — by pillar</h5>
            {detailError && <p className="rw-none">{detailError}</p>}
            {!detail && !detailError && <p className="rw-none">Explaining…</p>}
            {detail && (
              <>
                <div className="rw-pillars">
                  {pillarBars.map((bar) => {
                    const w = (Math.abs(bar.value) / pillarMax) * 50;
                    const up = bar.value >= 0;
                    return (
                      <div key={bar.label} className="rw-pillar" title={`${bar.label}: ${points(bar.value)}`}>
                        <span className="rw-pillar-label">{bar.label}</span>
                        <span className="rw-pillar-track">
                          <span className="rw-pillar-axis" />
                          <span
                            className="rw-pillar-bar"
                            style={{
                              width: `${w}%`,
                              left: up ? "50%" : `${50 - w}%`,
                              background: up ? colors.up : colors.down,
                              borderRadius: up ? "0 4px 4px 0" : "4px 0 0 4px",
                            }}
                          />
                        </span>
                        <span className="rw-pillar-value">{points(bar.value)}</span>
                      </div>
                    );
                  })}
                </div>
                <p className="rw-legend-line">
                  <span style={{ color: colors.up }}>■</span> pushes towards failure <span style={{ color: colors.down }}>■</span> pushes towards survival · base
                  rate {pct(detail.base)}
                </p>
              </>
            )}
          </div>

          <div className="rw-profile-drivers">
            <h5>Top drivers — where this {noun} sits among its peers</h5>
            {drivers.map((d) => {
              const value = selected.record[d.feature];
              const numeric = typeof value === "number" && peers.sorted[d.feature];
              const p = numeric ? percentileOf(peers.sorted[d.feature], value as number) : null;
              const fm = peers.failedMedian[d.feature];
              const fp = numeric && Number.isFinite(fm) ? percentileOf(peers.sorted[d.feature], fm) : null;
              return (
                <div key={d.feature} className="rw-driver">
                  <span className="rw-driver-name">{featureLabel(scenario, d.feature)}</span>
                  <span className="rw-driver-value">{typeof value === "number" ? compact(value) : String(value)}</span>
                  <span className="rw-driver-effect" style={{ color: d.value >= 0 ? colors.up : colors.down }}>
                    {points(d.value)}
                  </span>
                  {p !== null && (
                    <span className="rw-driver-track" title={`Percentile among all ${noun}s: ${Math.round(p * 100)}`}>
                      {fp !== null && <span className="rw-driver-failed" style={{ left: `${fp * 100}%` }} title={`Median of ${noun}s that failed`} />}
                      <span className="rw-driver-dot" style={{ left: `${p * 100}%` }} />
                    </span>
                  )}
                </div>
              );
            })}
            {drivers.length > 0 && (
              <p className="rw-legend-line">
                Track = percentile among all {noun}s · <span className="rw-key-failed" /> median of {noun}s that failed
              </p>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
