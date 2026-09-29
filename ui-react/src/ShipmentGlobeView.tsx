import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { geoInterpolate } from "d3-geo";
import worldCountriesUrl from "world-atlas/countries-110m.json?url";
import worldLandUrl from "world-atlas/land-110m.json?url";
import type { Topology } from "topojson-specification";
import { datasetSample, modelCard, outOfFoldScores, predict, type ScenarioSummary, type ShipmentGlobeExtra, type ShipmentLever } from "./apiClient";
import { config } from "./config";
import { type GlobeArc, type GlobeColors, type GlobePlace, globeGeometry, GlobeScene } from "./globeScene";
import { fetchJsonOnce, type Glyph, useElementSize, usePrefersReducedMotion } from "./logisticsViz";
import { VehicleIcon } from "./VehicleIcon";
import { explainByFeature, featureLabel, type Record_ } from "./predictUtils";
import { RISK_RAMP, surfaceMode, TIER_GLYPHS, tierColor } from "./riskPalette";
import { useTheme } from "./useTheme";
import "./shipmentGlobe.css";

/**
 * Generic 5th workspace tab for a binary-classification tabular_ml scenario whose rows
 * are shipments from an origin to a destination and whose positive class is the
 * adverse outcome (`ui_extras: {kind: shipment_globe}`, see
 * scenario_schema.ShipmentGlobeExtra) — e.g. `global_health_shipments`.
 *
 * Every row carries its out-of-fold score (`model.out_of_fold_scores`: from a model that
 * never saw that row) and is sorted into the scenario's tiers. A rotating globe draws the shipments of the replay window as arcs
 * (all years at once = one arc per route, coloured by its mean risk), a timeline under
 * it replays the scheduled months, and a departures board lists the window's
 * shipments riskiest first. Picking one explains it (SHAP) and re-plans it: every
 * `levers` alternative is scored live by the deployed model, best first. The real
 * outcome can be revealed as a backtest — honest, because the scores are cross-fitted
 * (a view that falls back to the deployed model's in-sample scores says so).
 *
 * Colour: tiers use the shared validated ordinal red ramp (riskPalette.ts) with the
 * lowest tier recessive grey; a tier always also carries its glyph and label. The
 * departures board is a physical-looking dark object in every theme, so it uses the
 * dark-surface ramp.
 */

type Shipment = {
  index: number;
  id: string;
  name: string;
  detail: string;
  date: string;
  month: number;
  origin: [number, number];
  originLabel: string;
  destination: [number, number];
  destinationLabel: string;
  mode: string;
  glyph: Glyph;
  size: number;
  record: Record_;
  probability: number;
  tier: number;
  actual: 0 | 1;
  outcomeDetail: string | number | null;
};

type Loaded = { shipments: Shipment[]; months: string[]; cvAuc: number | null; holdoutAuc: number | null; oofAuc: number | null; crossFitted: boolean };
type Detail = { base: number; deployed: number; items: { feature: string; value: number }[] };
type Alternative = { lever: ShipmentLever; label: string; glyph: Glyph | null; probability: number };

const CACHE = new Map<string, Promise<Loaded>>();
const BOARD_ROWS = 60;
const SPEED: Record<Glyph, number> = { plane: 0.3, bolt: 0.3, van: 0.2, truck: 0.16, rail: 0.16, ship: 0.1, parcel: 0.2 };
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

function tierOf(probability: number, tiers: ShipmentGlobeExtra["tiers"]): number {
  let tier = 0;
  tiers.forEach((t, i) => {
    if (probability >= t.min_probability) tier = i;
  });
  return tier;
}

function monthKey(date: string): string {
  return date.slice(0, 7);
}

function monthLabel(key: string): string {
  const [y, m] = key.split("-");
  return `${MONTHS[Number(m) - 1]} ${y}`;
}

async function loadShipments(scenario: ScenarioSummary, extras: ShipmentGlobeExtra, accessToken: string | null): Promise<Loaded> {
  const features = scenario.feature_columns ?? [];
  const cardPromise = modelCard(config.predictionUrl, scenario.slug, accessToken).catch(() => null);
  const oofPromise = outOfFoldScores(config.predictionUrl, scenario.slug, accessToken).catch(() => null);
  const sample = await datasetSample(config.predictionUrl, scenario.slug, 30000, accessToken);
  const records = sample.rows.map((row) => Object.fromEntries(features.map((f) => [f, row[f] as number | string])));
  const oof = await oofPromise;
  const blind = new Map((oof?.rows ?? []).map((r) => [r.id, r.probability]));
  const idOf = (row: Record<string, unknown>, index: number) => (sample.id_column ? String(row[sample.id_column]) : String(index + 1));
  const crossFitted = sample.rows.length > 0 && sample.rows.every((row, i) => blind.has(idOf(row, i)));
  // Fallback (scores not trained yet): the deployed model's own, in-sample scores.
  const inSample = crossFitted ? null : await predict(config.predictionUrl, scenario.slug, records, accessToken, { explain: false });
  const keys = [...new Set(sample.rows.map((r) => monthKey(String(r[extras.date_column] ?? ""))))].filter(Boolean).sort();
  // A contiguous month axis (gaps included), so the replay's clock never jumps.
  const months: string[] = [];
  if (keys.length) {
    let [y, m] = keys[0].split("-").map(Number);
    const [ly, lm] = keys[keys.length - 1].split("-").map(Number);
    while (y < ly || (y === ly && m <= lm)) {
      months.push(`${y}-${String(m).padStart(2, "0")}`);
      m += 1;
      if (m > 12) {
        m = 1;
        y += 1;
      }
    }
  }
  const monthIndex = new Map(months.map((k, i) => [k, i]));
  const target = scenario.target ?? "";
  const shipments = sample.rows.map((row, index): Shipment => {
    const probability = crossFitted ? (blind.get(idOf(row, index)) as number) : inSample!.predictions[index].prediction;
    const mode = extras.mode_feature ? String(row[extras.mode_feature] ?? "") : "";
    const date = String(row[extras.date_column] ?? "");
    return {
      index,
      id: idOf(row, index),
      name: String(row[extras.name_column] ?? ""),
      detail: extras.detail_columns.map((c) => row[c]).filter((v) => v !== null && v !== "").join(" · "),
      date,
      month: monthIndex.get(monthKey(date)) ?? 0,
      origin: [Number(row[extras.origin_lon_column]), Number(row[extras.origin_lat_column])],
      originLabel: String(row[extras.origin_label_column] ?? ""),
      destination: [Number(row[extras.destination_lon_column]), Number(row[extras.destination_lat_column])],
      destinationLabel: String(row[extras.destination_label_column] ?? ""),
      mode,
      glyph: extras.mode_icons[mode] ?? "parcel",
      size: extras.size_feature ? Number(row[extras.size_feature]) : 1,
      record: records[index],
      probability,
      tier: tierOf(probability, extras.tiers),
      actual: Number(row[target]) === 1 ? 1 : 0,
      outcomeDetail: extras.outcome_detail_column ? row[extras.outcome_detail_column] : null,
    };
  });
  const card = await cardPromise;
  return {
    shipments,
    months,
    cvAuc: card?.metrics.cv_roc_auc_mean ?? null,
    holdoutAuc: card?.metrics.holdout_roc_auc ?? null,
    oofAuc: crossFitted ? oof?.roc_auc ?? null : null,
    crossFitted,
  };
}

function pct(p: number, digits = 0): string {
  return `${(p * 100).toFixed(digits)}%`;
}

function points(v: number): string {
  return `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pts`;
}

/** A feature value for display — years and small counts without thousands separators. */
function featureValue(value: string | number): string {
  if (typeof value !== "number") return String(value);
  return Math.abs(value) < 10000 ? String(Number(value.toFixed(2))) : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function money(v: number): string {
  if (!Number.isFinite(v)) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}k`;
  return `$${v.toFixed(0)}`;
}

/** Split-flap text: every character is its own tile, re-mounted (and flipped) on change. */
function FlapText({ text, className }: { text: string; className?: string }) {
  return (
    <span className={`sg-flap ${className ?? ""}`} aria-label={text}>
      {[...text].map((c, i) => (
        <span key={`${i}-${c}`} className={c === " " ? "sg-flap-gap" : "sg-flap-tile"} style={{ animationDelay: `${i * 28}ms` }} aria-hidden>
          {c}
        </span>
      ))}
    </span>
  );
}

export function ShipmentGlobeView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as ShipmentGlobeExtra;
  const { theme } = useTheme();
  const mode = surfaceMode(theme.cssVars["--bg"]);
  const reducedMotion = usePrefersReducedMotion();
  const noun = extras.shipment_noun;
  const features = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const schema = useMemo(() => scenario.feature_schema ?? {}, [scenario.feature_schema]);
  const flagTier = useMemo(() => {
    const i = extras.tiers.findIndex((t) => t.label === extras.flag_from_tier);
    return i >= 0 ? i : extras.tiers.length - 1;
  }, [extras]);
  const colorOf = useCallback((tier: number) => tierColor(mode, tier), [mode]);

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [month, setMonth] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [country, setCountry] = useState<string | null>(null);
  const [selected, setSelected] = useState<Shipment | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [alternatives, setAlternatives] = useState<Alternative[] | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [hover, setHover] = useState<{ place: GlobePlace; x: number; y: number } | null>(null);
  const [geometryReady, setGeometryReady] = useState(false);

  useEffect(() => {
    const key = `${scenario.slug}|${accessToken ?? ""}`;
    if (!CACHE.has(key)) CACHE.set(key, loadShipments(scenario, extras, accessToken));
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

  const shipments = useMemo(() => loaded?.shipments ?? [], [loaded]);
  const months = useMemo(() => loaded?.months ?? [], [loaded]);
  const byId = useMemo(() => new Map(shipments.map((s) => [s.id, s])), [shipments]);

  const inWindow = useMemo(
    () => shipments.filter((s) => (month === null || s.month === month) && (country === null || s.destinationLabel === country)),
    [shipments, month, country],
  );
  const board = useMemo(() => [...inWindow].sort((a, b) => b.probability - a.probability).slice(0, BOARD_ROWS), [inWindow]);

  const kpis = useMemo(() => {
    const flagged = inWindow.filter((s) => s.tier >= flagTier);
    const late = inWindow.filter((s) => s.actual === 1);
    const caught = flagged.filter((s) => s.actual === 1).length;
    return {
      n: inWindow.length,
      expected: inWindow.reduce((sum, s) => sum + s.probability, 0),
      flagged: flagged.length,
      late: late.length,
      caught,
      value: extras.size_feature ? inWindow.reduce((sum, s) => sum + (Number.isFinite(s.size) ? s.size : 0), 0) : null,
    };
  }, [inWindow, flagTier, extras.size_feature]);

  // Per-month tier stacks for the timeline (and real late counts for the reveal).
  const timeline = useMemo(() => {
    const stacks = months.map(() => ({ tiers: extras.tiers.map(() => 0), late: 0, n: 0 }));
    for (const s of shipments) {
      if (country !== null && s.destinationLabel !== country) continue;
      const bucket = stacks[s.month];
      bucket.tiers[s.tier] += 1;
      bucket.late += s.actual;
      bucket.n += 1;
    }
    return { stacks, max: Math.max(1, ...stacks.map((b) => b.n)) };
  }, [months, shipments, extras.tiers, country]);

  // ── globe content ────────────────────────────────────────────────────────────
  const arcs: GlobeArc[] = useMemo(() => {
    const nTiers = extras.tiers.length - 1;
    const sizeScale = (v: number) => (extras.size_feature ? Math.min(1.4, Math.log10(1 + Math.max(0, v)) / 5) : 0.4);
    const out: GlobeArc[] = [];
    if (month === null) {
      const routes = new Map<string, Shipment[]>();
      for (const s of inWindow) {
        const key = `${s.originLabel}→${s.destinationLabel}`;
        routes.set(key, [...(routes.get(key) ?? []), s]);
      }
      for (const [key, list] of routes) {
        const mean = list.reduce((sum, s) => sum + s.probability, 0) / list.length;
        const tier = tierOf(mean, extras.tiers);
        const glyphCount = new Map<Glyph, number>();
        list.forEach((s) => glyphCount.set(s.glyph, (glyphCount.get(s.glyph) ?? 0) + 1));
        const glyph = [...glyphCount].sort((a, b) => b[1] - a[1])[0][0];
        const lateShare = list.reduce((sum, s) => sum + s.actual, 0) / list.length;
        out.push({
          id: `route:${key}`,
          origin: list[0].origin,
          destination: list[0].destination,
          color: colorOf(tier),
          emphasis: tier / nTiers,
          width: 0.7 + Math.min(2.6, Math.log10(1 + list.length) * 1.1),
          glyph,
          speed: SPEED[glyph] * 0.7,
          late: lateShare >= 0.3,
        });
      }
    } else {
      for (const s of inWindow) {
        out.push({
          id: s.id,
          origin: s.origin,
          destination: s.destination,
          color: colorOf(s.tier),
          emphasis: s.tier / nTiers,
          width: 0.9 + 0.5 * s.tier + sizeScale(s.size),
          glyph: s.glyph,
          speed: SPEED[s.glyph],
          late: s.actual === 1,
        });
      }
    }
    if (selected && !out.some((a) => a.id === selected.id)) {
      out.push({ id: selected.id, origin: selected.origin, destination: selected.destination, color: colorOf(selected.tier), emphasis: 1, width: 3, glyph: selected.glyph, speed: SPEED[selected.glyph], late: selected.actual === 1 });
    }
    return out;
  }, [inWindow, month, extras, colorOf, selected]);

  const places: GlobePlace[] = useMemo(() => {
    const dest = new Map<string, { s: Shipment; n: number; p: number }>();
    const orig = new Map<string, { s: Shipment; n: number }>();
    for (const s of inWindow) {
      const d = dest.get(s.destinationLabel) ?? { s, n: 0, p: 0 };
      d.n += 1;
      d.p += s.probability;
      dest.set(s.destinationLabel, d);
      const o = orig.get(s.originLabel) ?? { s, n: 0 };
      o.n += 1;
      orig.set(s.originLabel, o);
    }
    const scale = month === null ? 0.16 : 1.1;
    const destTop = new Set([...dest].sort((a, b) => b[1].n - a[1].n).slice(0, 9).map(([k]) => k));
    const origTop = new Set([...orig].sort((a, b) => b[1].n - a[1].n).slice(0, 4).map(([k]) => k));
    return [
      ...[...orig].map(([label, o]): GlobePlace => ({
        id: `o:${label}`,
        lon: o.s.origin[0],
        lat: o.s.origin[1],
        label,
        kind: "origin",
        size: Math.min(3, Math.sqrt(o.n) * scale * 0.4),
        color: "",
        showLabel: origTop.has(label),
      })),
      ...[...dest].map(([label, d]): GlobePlace => ({
        id: `d:${label}`,
        lon: d.s.destination[0],
        lat: d.s.destination[1],
        label,
        kind: "destination",
        size: Math.min(12, Math.sqrt(d.n) * scale),
        color: colorOf(tierOf(d.p / d.n, extras.tiers)),
        showLabel: destTop.has(label) || label === country,
      })),
    ];
  }, [inWindow, month, colorOf, extras.tiers, country]);

  const globeColors: GlobeColors = useMemo(() => {
    const accent = theme.cssVars["--accent"] ?? "#33c7ff";
    return mode === "dark"
      ? { ocean: "#0d2544", oceanEdge: "#040b18", land: "#1c3657", border: "rgba(160,190,225,0.2)", graticule: "rgba(127,163,201,0.09)", ink: "#e8f1fb", dim: "#9fb3cc", accent, halo: "#050b16", ring: RISK_RAMP.dark.ring }
      : { ocean: "#e9f1f9", oceanEdge: "#c6d6e9", land: "#fdfefe", border: "rgba(40,70,110,0.2)", graticule: "rgba(22,32,43,0.07)", ink: "#0f1b2a", dim: "#4e6378", accent, halo: "#f6f8fb", ring: RISK_RAMP.light.ring };
  }, [mode, theme]);

  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<GlobeScene | null>(null);
  const [stageBox, stageSize] = useElementSize<HTMLDivElement>();

  const select = useCallback((s: Shipment | null) => setSelected(s), []);
  const selectById = useCallback(
    (id: string | null) => {
      if (id === null || id.startsWith("route:")) {
        if (id?.startsWith("route:")) {
          const [, route] = id.split("route:");
          const destination = route.split("→")[1];
          setCountry((c) => (c === destination ? null : destination));
        } else setSelected(null);
        return;
      }
      setSelected(byId.get(id) ?? null);
    },
    [byId],
  );

  useEffect(() => {
    if (!canvas.current) return;
    const s = new GlobeScene(canvas.current, {
      onSelectArc: (id) => selectRef.current(id),
      onHoverPlace: (place, x, y) => setHover(place ? { place, x, y } : null),
      onClickPlace: (place) => {
        if (place.kind === "destination") setCountry((c) => (c === place.label ? null : place.label));
      },
    });
    scene.current = s;
    return () => s.destroy();
  }, [loaded]);
  const selectRef = useRef(selectById);
  useEffect(() => {
    selectRef.current = selectById;
  }, [selectById]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchJsonOnce<Topology>(worldLandUrl), fetchJsonOnce<Topology>(worldCountriesUrl)])
      .then(([land, countries]) => {
        if (cancelled || !scene.current) return;
        scene.current.setGeometry(globeGeometry(land, countries));
        setGeometryReady(true);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [loaded]);
  useEffect(() => scene.current?.resize(stageSize.width, stageSize.height), [stageSize, loaded]);
  useEffect(() => {
    scene.current?.setView({ mode, colors: globeColors, arcs, places, selectedId: selected?.id ?? null, revealed, reducedMotion });
  }, [mode, globeColors, arcs, places, selected, revealed, reducedMotion, loaded, geometryReady]);

  // Face the selected shipment.
  useEffect(() => {
    if (!selected) return;
    const mid = geoInterpolate(selected.origin, selected.destination)(0.5);
    scene.current?.focus(mid[0], mid[1]);
  }, [selected]);

  // Replay.
  useEffect(() => {
    if (!playing || months.length === 0) return;
    const id = window.setInterval(() => setMonth((m) => Math.min(months.length - 1, m === null ? 0 : m + 1)), reducedMotion ? 1600 : 1100);
    return () => window.clearInterval(id);
  }, [playing, months.length, reducedMotion]);
  useEffect(() => {
    if (playing && month === months.length - 1) setPlaying(false);
  }, [playing, month, months.length]);

  // Explain + re-plan the selected shipment.
  useEffect(() => {
    setDetail(null);
    setAlternatives(null);
    setDetailError(null);
    if (!selected) return;
    let cancelled = false;
    predict(config.predictionUrl, scenario.slug, [selected.record], accessToken)
      .then((response) => {
        if (cancelled) return;
        const p = response.predictions[0];
        setDetail({ ...explainByFeature(features, p.prediction, p.contributions), deployed: p.prediction });
      })
      .catch((e: Error) => !cancelled && setDetailError(e.message));
    const alts: Omit<Alternative, "probability">[] = [];
    const records: Record_[] = [];
    for (const lever of extras.levers) {
      const spec = schema[lever.feature];
      const current = selected.record[lever.feature];
      if (lever.deltas) {
        for (const d of lever.deltas) {
          alts.push({ lever, label: `${d > 0 ? "+" : "−"}${Math.abs(d)} ${lever.delta_units}`.trim(), glyph: null });
          records.push({ ...selected.record, [lever.feature]: Number(current) + d });
        }
      } else if (spec?.type === "categorical") {
        for (const option of lever.options ?? spec.options) {
          if (option === current) continue;
          alts.push({ lever, label: option, glyph: lever.feature === extras.mode_feature ? extras.mode_icons[option] ?? null : null });
          records.push({ ...selected.record, [lever.feature]: option });
        }
      }
    }
    if (records.length) {
      predict(config.predictionUrl, scenario.slug, records, accessToken, { explain: false })
        .then((response) => {
          if (!cancelled) setAlternatives(alts.map((a, i) => ({ ...a, probability: response.predictions[i].prediction })));
        })
        .catch((e: Error) => !cancelled && setDetailError(e.message));
    } else setAlternatives([]);
    return () => {
      cancelled = true;
    };
  }, [selected, scenario.slug, accessToken, features, extras, schema]);

  // Re-planning compares the deployed model with itself: the same record as it stands
  // (detail.deployed) against each one-change alternative.
  const deployed = detail?.deployed ?? null;
  const bestMove = useMemo(
    () => (alternatives && deployed !== null ? [...alternatives].sort((a, b) => a.probability - b.probability).find((a) => a.probability < deployed) ?? null : null),
    [alternatives, deployed],
  );

  useCopilotReadable({
    description: `${scenario.title} globe: every ${noun} scored for the risk of "${extras.outcome_label}", the replay window the user is looking at, and the ${noun} they selected with its re-planning options. Use it to answer which ${noun}s are riskiest now, why, and what change would help most.`,
    value: loaded
      ? {
          window: month === null ? "all years" : monthLabel(months[month]),
          destination_filter: country,
          shipments_in_window: kpis.n,
          expected_adverse: Number(kpis.expected.toFixed(1)),
          flagged: kpis.flagged,
          riskiest: board.slice(0, 8).map((s) => ({ item: s.name, route: `${s.originLabel} → ${s.destinationLabel}`, mode: s.mode, probability: Number(s.probability.toFixed(3)) })),
          selected: selected
            ? {
                item: selected.name,
                route: `${selected.originLabel} → ${selected.destinationLabel}`,
                mode: selected.mode,
                scheduled: selected.date,
                probability: Number(selected.probability.toFixed(3)),
                tier: extras.tiers[selected.tier].label,
                top_drivers: detail?.items.slice(0, 5).map((i) => ({ feature: featureLabel(scenario, i.feature), points: Number((i.value * 100).toFixed(1)) })),
                alternatives: alternatives?.map((a) => ({ change: `${a.lever.label ?? featureLabel(scenario, a.lever.feature)}: ${a.label}`, probability: Number(a.probability.toFixed(3)) })),
                ...(revealed ? { actual: selected.actual ? extras.outcome_label : "on time" } : {}),
              }
            : null,
          outcomes_revealed: revealed,
        }
      : null,
  });

  if (error) return <div className="sg-empty">Could not load the globe: {error}</div>;
  if (!loaded) {
    return (
      <div className="sg-empty sg-loading">
        <span className="sg-spinner" /> Scoring every {noun}…
      </div>
    );
  }

  const windowLabel = month === null ? `${months[0]?.slice(0, 4)} — ${months[months.length - 1]?.slice(0, 4)}` : monthLabel(months[month]);
  const selectedTier = selected ? extras.tiers[selected.tier] : null;
  const altGroups = extras.levers.map((lever) => ({ lever, items: (alternatives ?? []).filter((a) => a.lever === lever) }));
  const driverMax = Math.max(1e-6, ...(detail?.items.slice(0, 6).map((i) => Math.abs(i.value)) ?? [0]));

  return (
    <div className={`sg sg--${mode}`}>
      <header className="sg-head">
        <div>
          <h3>{extras.title}</h3>
          {extras.subtitle && <p>{extras.subtitle}</p>}
        </div>
        <div className="sg-model">
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
            <span title="ROC AUC of the cross-fitted scores drawn on this tab — every shipment scored by a model that never saw it.">
              on the globe <b>{loaded.oofAuc.toFixed(3)}</b> <em>out-of-fold</em>
            </span>
          )}
        </div>
      </header>

      <div className="sg-kpis">
        <div className="sg-kpi">
          <span>{month === null ? "All years" : "Scheduled in"}</span>
          <b>{windowLabel}</b>
        </div>
        <div className="sg-kpi">
          <span>{noun[0].toUpperCase() + noun.slice(1)}s{country ? ` to ${country}` : ""}</span>
          <b>{kpis.n.toLocaleString()}</b>
          {kpis.value !== null && <em>{money(kpis.value)} of goods</em>}
        </div>
        <div className="sg-kpi">
          <span>Expected {extras.outcome_label.toLowerCase()}</span>
          <b>{kpis.expected.toLocaleString(undefined, { maximumFractionDigits: kpis.n > 100 ? 0 : 1 })}</b>
          <em>{kpis.n ? pct(kpis.expected / kpis.n, 1) : "—"} · sum of risks</em>
        </div>
        <div className="sg-kpi sg-kpi--flag" style={{ ["--tier" as string]: colorOf(flagTier) }}>
          <span>
            {TIER_GLYPHS[flagTier]} {extras.tiers[flagTier].label}
            {flagTier < extras.tiers.length - 1 ? " or above" : ""}
          </span>
          <b>{kpis.flagged.toLocaleString()}</b>
          <em>{kpis.n ? pct(kpis.flagged / kpis.n, 1) : "—"} flagged for action</em>
        </div>
        <div className={`sg-kpi sg-reveal${revealed ? " sg-reveal--on" : ""}`}>
          <label className="sg-switch">
            <input type="checkbox" checked={revealed} onChange={(e) => setRevealed(e.target.checked)} />
            <span className="sg-switch-track" />
            Reveal what happened
          </label>
          {revealed ? (
            <em>
              <b>{kpis.late}</b> {extras.outcome_label.toLowerCase()} · <b>{kpis.caught}</b> of them flagged ({kpis.late ? pct(kpis.caught / kpis.late) : "—"}) · precision{" "}
              {kpis.flagged ? pct(kpis.caught / kpis.flagged) : "—"}
            </em>
          ) : (
            <em>
              Judge the model first — then see which really were late.{" "}
              {loaded.crossFitted ? "Every score is cross-fitted: no shipment was scored by a model that saw it." : "In-sample scores: cross-fitted ones are not trained yet."}
            </em>
          )}
        </div>
      </div>

      <div className="sg-main">
        <section className="sg-stage-wrap">
          <div className="sg-stage" ref={stageBox}>
            <canvas ref={canvas} aria-label={`Globe of ${noun}s coloured by risk`} />
            <div className="sg-stage-tools">
              <button type="button" onClick={() => scene.current?.zoomBy(1.2)} aria-label="Zoom in">
                +
              </button>
              <button type="button" onClick={() => scene.current?.zoomBy(1 / 1.2)} aria-label="Zoom out">
                −
              </button>
            </div>
            <div className="sg-legend">
              {extras.tiers.map((t, i) => (
                <span key={t.label}>
                  <i style={{ background: colorOf(i) }} /> {TIER_GLYPHS[i]} {t.label}
                </span>
              ))}
              {revealed && (
                <span>
                  <i className="sg-legend-dash" /> {month === null ? `route ≥30% ${extras.outcome_label.toLowerCase()}` : extras.outcome_label}
                </span>
              )}
            </div>
            {country && (
              <button type="button" className="sg-filter-chip" onClick={() => setCountry(null)}>
                {country} ✕
              </button>
            )}
            {hover && (
              <div className="sg-tip" style={{ left: hover.x, top: hover.y }}>
                <b>{hover.place.label}</b>
                {(() => {
                  const list = inWindow.filter((s) => (hover.place.kind === "destination" ? s.destinationLabel : s.originLabel) === hover.place.label);
                  const expected = list.reduce((sum, s) => sum + s.probability, 0);
                  const late = list.reduce((sum, s) => sum + s.actual, 0);
                  return (
                    <>
                      <span>
                        {list.length.toLocaleString()} {noun}s {hover.place.kind === "destination" ? "received" : "shipped"} · {list.length ? pct(expected / list.length) : "—"} mean risk
                      </span>
                      {revealed && <span>{late} {extras.outcome_label.toLowerCase()}</span>}
                      {hover.place.kind === "destination" && <span className="sg-tip-hint">click to {country === hover.place.label ? "clear the filter" : "filter"}</span>}
                    </>
                  );
                })()}
              </div>
            )}
          </div>
          <p className="sg-note">
            Arcs run from the manufacturing site to the destination's capital (a map anchor, not the delivery address).{" "}
            {month === null ? "All years: one arc per route, coloured by its mean risk — press play to replay month by month." : "Drag to turn the globe; click an arc or a board row."}
          </p>

          <div className="sg-timeline">
            <div className="sg-timeline-controls">
              <button type="button" className="sg-play" onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause replay" : "Play replay"}>
                {playing ? "❚❚" : "▶"}
              </button>
              <button type="button" className={`sg-all${month === null ? " sg-all--on" : ""}`} onClick={() => { setPlaying(false); setMonth(null); }}>
                All years
              </button>
            </div>
            <div
              className="sg-bars"
              onClick={(e) => {
                const rect = e.currentTarget.getBoundingClientRect();
                const i = Math.min(months.length - 1, Math.max(0, Math.floor(((e.clientX - rect.left) / rect.width) * months.length)));
                setPlaying(false);
                setMonth(i);
              }}
              role="slider"
              aria-label="Scheduled month"
              aria-valuemin={0}
              aria-valuemax={months.length - 1}
              aria-valuenow={month ?? undefined}
              aria-valuetext={month === null ? "all years" : monthLabel(months[month])}
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight") setMonth((m) => Math.min(months.length - 1, (m ?? -1) + 1));
                if (e.key === "ArrowLeft") setMonth((m) => Math.max(0, (m ?? 1) - 1));
              }}
            >
              <svg viewBox={`0 0 ${months.length * 10} 60`} preserveAspectRatio="none">
                {timeline.stacks.map((bucket, i) => {
                  let y = 60;
                  return (
                    <g key={months[i]} opacity={month === null || month === i ? 1 : 0.45}>
                      <title>
                        {monthLabel(months[i])}: {bucket.n} {noun}s{revealed ? `, ${bucket.late} ${extras.outcome_label.toLowerCase()}` : ""}
                      </title>
                      <rect x={i * 10} y={0} width={10} height={60} fill="transparent" />
                      {bucket.tiers.map((count, t) => {
                        const h = (count / timeline.max) * 56;
                        y -= h;
                        return h > 0 ? <rect key={t} x={i * 10 + 1} y={y} width={8} height={Math.max(0, h - 0.6)} fill={colorOf(t)} /> : null;
                      })}
                      {revealed && bucket.late > 0 && <rect x={i * 10} y={60 - (bucket.late / timeline.max) * 56 - 1} width={10} height={2} fill={globeColors.ring} />}
                    </g>
                  );
                })}
              </svg>
              {month !== null && <span className="sg-playhead" style={{ left: `${((month + 0.5) / months.length) * 100}%` }} />}
              <div className="sg-years">
                {months.map((m, i) =>
                  m.endsWith("-01") ? (
                    <span key={m} style={{ left: `${(i / months.length) * 100}%` }}>
                      {m.slice(0, 4)}
                    </span>
                  ) : null,
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="sg-board" aria-label="Departures board">
          <div className="sg-board-head">
            <span className="sg-board-title">DEPARTURES</span>
            <FlapText text={(month === null ? "ALL YEARS" : monthLabel(months[month])).toUpperCase()} />
          </div>
          <div className="sg-board-cols">
            <span />
            <span>{noun.toUpperCase()}</span>
            <span>ROUTE</span>
            <span className="sg-num">RISK</span>
          </div>
          <div className="sg-board-rows">
            {board.map((s, i) => (
              <button
                type="button"
                key={`${month ?? "all"}-${country ?? ""}-${s.id}`}
                className={`sg-row${selected?.id === s.id ? " sg-row--on" : ""}`}
                style={{ animationDelay: `${Math.min(i, 18) * 32}ms`, ["--tier" as string]: tierColor("dark", s.tier) }}
                onClick={() => select(selected?.id === s.id ? null : s)}
              >
                <span className="sg-row-mode" title={s.mode}>
                  <VehicleIcon glyph={s.glyph} size={15} />
                </span>
                <span className="sg-row-name">
                  <b title={s.name}>{s.name}</b>
                  <em>
                    #{s.id} · {s.date}
                  </em>
                </span>
                <span className="sg-row-route">
                  {s.originLabel} → {s.destinationLabel}
                </span>
                <span className="sg-row-risk">
                  <span>
                    <span className="sg-row-tier" title={extras.tiers[s.tier].label}>
                      {TIER_GLYPHS[s.tier]}
                    </span>
                    {pct(s.probability)}
                  </span>
                  {revealed && <span className={s.actual ? "sg-row-late" : "sg-row-ok"}>{s.actual ? `LATE${s.outcomeDetail !== null ? ` +${s.outcomeDetail}` : ""}` : "ON TIME"}</span>}
                </span>
              </button>
            ))}
            {board.length === 0 && <p className="sg-board-empty">No {noun}s scheduled in this window.</p>}
          </div>
          <p className="sg-board-foot">
            Riskiest first · showing {board.length} of {inWindow.length.toLocaleString()} · click a row to explain and re-plan it
          </p>
        </section>
      </div>

      {selected && selectedTier && (
        <section className="sg-dossier" style={{ ["--tier" as string]: colorOf(selected.tier) }}>
          <div className="sg-dossier-id">
            <span className="sg-badge">
              {TIER_GLYPHS[selected.tier]} {selectedTier.label}
            </span>
            <h4>{selected.name}</h4>
            <p>
              <VehicleIcon glyph={selected.glyph} size={14} /> {selected.mode} · {selected.originLabel} → {selected.destinationLabel} · scheduled {selected.date}
            </p>
            <p className="sg-dim">
              {selected.detail} · #{selected.id}
            </p>
            <div className="sg-hero">
              <span className="sg-hero-value">{pct(selected.probability, 1)}</span>
              <span className="sg-hero-label">
                risk: {extras.outcome_label.toLowerCase()}
                <br />
                {loaded.crossFitted ? "scored blind — by a model that never saw it" : "deployed model, in-sample"}
              </span>
            </div>
            {selectedTier.description && <p className="sg-action">{selectedTier.description}</p>}
            {revealed && (
              <p className={`sg-outcome${selected.actual ? " sg-outcome--bad" : ""}`}>
                {selected.actual
                  ? `✖ ${extras.outcome_label}${selected.outcomeDetail !== null ? ` — ${selected.outcomeDetail} ${extras.outcome_detail_units}` : ""}`
                  : `✓ ${scenario.target_value_labels?.["0"] ?? "On time"}`}
              </p>
            )}
            <button type="button" className="sg-close" onClick={() => select(null)}>
              Close
            </button>
          </div>

          <div className="sg-dossier-why">
            <h5>Why — the biggest pushes on this {noun}'s risk</h5>
            {detailError && <p className="sg-dim">{detailError}</p>}
            {!detail && !detailError && <p className="sg-dim">Explaining…</p>}
            {detail &&
              detail.items.slice(0, 6).map((item) => {
                const w = (Math.abs(item.value) / driverMax) * 50;
                const up = item.value >= 0;
                const value = selected.record[item.feature];
                return (
                  <div key={item.feature} className="sg-driver">
                    <span className="sg-driver-name">
                      {featureLabel(scenario, item.feature)}
                      <em>{featureValue(value)}</em>
                    </span>
                    <span className="sg-driver-track">
                      <span className="sg-driver-axis" />
                      <span
                        className="sg-driver-bar"
                        style={{ width: `${w}%`, left: up ? "50%" : `${50 - w}%`, background: up ? RISK_RAMP[mode].up : RISK_RAMP[mode].down, borderRadius: up ? "0 4px 4px 0" : "4px 0 0 4px" }}
                      />
                    </span>
                    <span className="sg-driver-value" style={{ color: up ? RISK_RAMP[mode].up : RISK_RAMP[mode].down }}>
                      {points(item.value)}
                    </span>
                  </div>
                );
              })}
            {detail && (
              <p className="sg-dim sg-small">
                <span style={{ color: RISK_RAMP[mode].up }}>■</span> raises the risk <span style={{ color: RISK_RAMP[mode].down }}>■</span> lowers it · base rate{" "}
                {pct(detail.base, 1)}
              </p>
            )}
          </div>

          <div className="sg-dossier-plan">
            <h5>Re-plan — one change at a time, scored live</h5>
            {deployed !== null && (
              <p className="sg-dim sg-small">
                Deployed model, as planned: <b>{pct(deployed, 1)}</b> — every alternative below is scored by the same model.
              </p>
            )}
            {!alternatives && !detailError && <p className="sg-dim">Scoring alternatives…</p>}
            {bestMove && (
              <div className="sg-best">
                <span>Best single change</span>
                <b>
                  {bestMove.glyph && <VehicleIcon glyph={bestMove.glyph} size={15} />} {bestMove.lever.label ?? featureLabel(scenario, bestMove.lever.feature)}: {bestMove.label}
                </b>
                <em>
                  {pct(deployed ?? selected.probability, 1)} → {pct(bestMove.probability, 1)} ({points(bestMove.probability - (deployed ?? selected.probability))})
                </em>
              </div>
            )}
            {alternatives && !bestMove && alternatives.length > 0 && <p className="sg-dim">None of these changes lowers the model's risk for this {noun}.</p>}
            {altGroups.map(({ lever, items }) =>
              items.length ? (
                <div key={lever.feature} className="sg-lever">
                  <span className="sg-lever-name">
                    {lever.label ?? featureLabel(scenario, lever.feature)}
                    <em>now: {String(selected.record[lever.feature])}</em>
                  </span>
                  <div className="sg-alts">
                    {items.map((a) => {
                      const delta = a.probability - (deployed ?? selected.probability);
                      const tier = tierOf(a.probability, extras.tiers);
                      return (
                        <span key={a.label} className={`sg-alt${a === bestMove ? " sg-alt--best" : ""}`} style={{ ["--alt" as string]: colorOf(tier) }}>
                          {a.glyph && <VehicleIcon glyph={a.glyph} size={13} />}
                          {a.label}
                          <b>{pct(a.probability)}</b>
                          <em className={delta < 0 ? "sg-down" : delta > 0 ? "sg-up" : ""}>{points(delta)}</em>
                        </span>
                      );
                    })}
                  </div>
                </div>
              ) : null,
            )}
            <p className="sg-dim sg-small">Each alternative changes one input and keeps everything else — the model's what-if, not a guarantee.</p>
          </div>
        </section>
      )}

      {extras.disclaimer && <p className="sg-disclaimer">{extras.disclaimer}</p>}
    </div>
  );
}
