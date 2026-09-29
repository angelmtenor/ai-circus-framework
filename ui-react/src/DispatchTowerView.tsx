import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import usStatesUrl from "us-atlas/states-10m.json?url";
import worldLandUrl from "world-atlas/land-110m.json?url";
import type { Topology } from "topojson-specification";
import { datasetSample, modelCard, predict, type DispatchTowerExtra, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import {
  HubMapScene,
  LandscapeScene,
  mapGeometry,
  type Flight,
  type HubPoint,
  type Landscape,
  type LandscapeReading,
  type Offset,
  type SceneColors,
} from "./dispatchScene";
import { CATEGORICAL, fetchJsonOnce, type Glyph, sequentialColor, useElementSize, usePrefersReducedMotion } from "./logisticsViz";
import { VehicleIcon } from "./VehicleIcon";
import { featureLabel } from "./predictUtils";
import { surfaceMode } from "./riskPalette";
import { useTheme } from "./useTheme";
import "./dispatchTower.css";

/**
 * Generic 5th workspace tab for a regression tabular_ml scenario that predicts how long
 * a shipment takes (`ui_extras: {kind: dispatch_tower}`, see
 * scenario_schema.DispatchTowerExtra) — e.g. `supply_chain`. Three linked views, all
 * scored by the scenario's own /predict:
 *
 * 1. **Network map** — the hubs, with the scenario's real shipments in flight around
 *    them (drawn at their offset from the hub: a schematic, the data has no real
 *    destinations). Click a hub to focus it.
 * 2. **ETA landscape** — a `grid_size`² grid of destinations around the focused hub,
 *    scored in one batched call for the chosen options and drawn as isochrones; the
 *    hub's real shipments are dotted on top. Hover reads it, click pins a destination.
 * 3. **The race** — every combination of the `choice_features` options to the pinned
 *    destination, each with its 90% prediction interval: the fastest option, the
 *    safest promise date (lowest upper bound) and what choosing well is worth.
 *
 * Colour: the landscape is a one-hue sequential blue on a *fixed* domain (the target's
 * 5th-95th percentile), so switching hub or options never silently re-scales it; race
 * lanes take the categorical slots by the first choice feature and are always
 * direct-labelled.
 */

type Row = Record<string, string | number | null>;
type Loaded = { rows: Row[]; r2: number | null; domain: [number, number] };
type RaceEntry = { combo: Record<string, string>; days: number; lower: number | null; upper: number | null };

const CACHE = new Map<string, Promise<Loaded>>();

function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return NaN;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

async function load(scenario: ScenarioSummary, accessToken: string | null): Promise<Loaded> {
  const cardPromise = modelCard(config.predictionUrl, scenario.slug, accessToken).catch(() => null);
  const sample = await datasetSample(config.predictionUrl, scenario.slug, 30000, accessToken);
  const target = scenario.target ?? "";
  const days = sample.rows.map((r) => Number(r[target])).filter(Number.isFinite).sort((a, b) => a - b);
  const card = await cardPromise;
  return { rows: sample.rows, r2: card?.metrics.holdout_r2 ?? null, domain: [Math.floor(quantile(days, 0.05)), Math.ceil(quantile(days, 0.95))] };
}

function cartesian(features: string[], options: Record<string, string[]>): Record<string, string>[] {
  return features.reduce<Record<string, string>[]>(
    (acc, feature) => acc.flatMap((combo) => (options[feature] ?? []).map((o) => ({ ...combo, [feature]: o }))),
    [{}],
  );
}

function fmtDays(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || !Number.isFinite(v) ? "—" : `${v.toFixed(digits)} d`;
}

export function DispatchTowerView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as DispatchTowerExtra;
  const schema = useMemo(() => scenario.feature_schema ?? {}, [scenario.feature_schema]);
  const features = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const { theme } = useTheme();
  const mode = surfaceMode(theme.cssVars["--bg"]);
  const reducedMotion = usePrefersReducedMotion();
  const units = scenario.target_units ?? "days";
  const noun = extras.shipment_noun;

  const colors: SceneColors = useMemo(
    () => ({
      land: theme.cssVars["--panel-2"] ?? "#101b36",
      border: theme.cssVars["--border"] ?? "#1c3a5e",
      outline: theme.cssVars["--border-strong"] ?? "#2c5a8a",
      ink: theme.cssVars["--text-h"] ?? "#f3fbff",
      dim: theme.cssVars["--dim"] ?? "#7fa3c9",
      accent: theme.cssVars["--accent"] ?? "#33c7ff",
      surface: theme.cssVars["--panel"] ?? "#0b1224",
    }),
    [theme],
  );

  const optionsOf = useCallback((feature: string): string[] => {
    const spec = schema[feature];
    return spec && spec.type === "categorical" ? spec.options : [];
  }, [schema]);
  const choiceOptions = useMemo(
    () => Object.fromEntries(extras.choice_features.map((f) => [f, optionsOf(f)])),
    [extras.choice_features, optionsOf],
  );
  const contextFeatures = useMemo(
    () => features.filter((f) => f !== extras.hub_feature && f !== extras.offset_x_feature && f !== extras.offset_y_feature && !extras.choice_features.includes(f)),
    [features, extras],
  );
  const hubLabel = useCallback((key: string) => extras.hubs.find((h) => h.key === key)?.label ?? key, [extras.hubs]);
  const glyphFor = useCallback(
    (combo: Record<string, string>): Glyph => {
      for (const f of [...extras.choice_features].reverse()) {
        const g = extras.choice_icons[combo[f]];
        if (g) return g;
      }
      return "parcel";
    },
    [extras],
  );

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [geometry, setGeometry] = useState<ReturnType<typeof mapGeometry> | null>(null);
  const [hub, setHub] = useState(extras.hubs[0].key);
  const [choice, setChoice] = useState<Record<string, string>>(() =>
    Object.fromEntries(extras.choice_features.map((f) => [f, String(schema[f]?.default ?? optionsOf(f)[0])])),
  );
  const [context, setContext] = useState<Record<string, string | number>>(() =>
    Object.fromEntries(contextFeatures.map((f) => [f, schema[f]?.default ?? ""])),
  );
  const [pin, setPin] = useState<Offset>({ dx: 240, dy: 160 });
  const [landscape, setLandscape] = useState<Landscape | null>(null);
  const [scoring, setScoring] = useState(false);
  const [reading, setReading] = useState<LandscapeReading | null>(null);
  const [race, setRace] = useState<RaceEntry[] | null>(null);
  const [raceRun, setRaceRun] = useState(0);
  const [raceStarted, setRaceStarted] = useState(false);
  const [raceOrder, setRaceOrder] = useState<"grouped" | "fastest">("grouped");
  const [showDots, setShowDots] = useState(true);

  useEffect(() => {
    const key = `${scenario.slug}|${accessToken ?? ""}`;
    if (!CACHE.has(key)) CACHE.set(key, load(scenario, accessToken));
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
  }, [scenario, accessToken]);

  useEffect(() => {
    let cancelled = false;
    fetchJsonOnce<Topology>(extras.map_scope === "usa" ? usStatesUrl : worldLandUrl)
      .then((topology) => !cancelled && setGeometry(mapGeometry(topology, extras.map_scope)))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [extras.map_scope]);

  const target = scenario.target ?? "";
  const flights: Flight[] = useMemo(
    () =>
      (loaded?.rows ?? []).map((r) => ({
        hub: String(r[extras.hub_feature]),
        dx: Number(r[extras.offset_x_feature]),
        dy: Number(r[extras.offset_y_feature]),
        days: Number(r[target]),
      })),
    [loaded, extras, target],
  );
  const hubPoints: HubPoint[] = useMemo(
    () =>
      extras.hubs.map((h) => {
        const days = flights.filter((f) => f.hub === h.key).map((f) => f.days).sort((a, b) => a - b);
        return { key: h.key, label: h.label ?? h.key, lat: h.lat, lon: h.lon, count: days.length, medianDays: quantile(days, 0.5) };
      }),
    [extras.hubs, flights],
  );
  const offsetBounds = useMemo(() => {
    const sx = schema[extras.offset_x_feature];
    const sy = schema[extras.offset_y_feature];
    const bx: [number, number] = sx && sx.type === "numeric" ? [sx.min, sx.max] : [-500, 500];
    const by: [number, number] = sy && sy.type === "numeric" ? [sy.min, sy.max] : [-500, 500];
    return { x: bx, y: by, max: Math.max(...bx.map(Math.abs), ...by.map(Math.abs)) };
  }, [schema, extras]);

  const baseRecord = useCallback(
    (offset: Offset, combo: Record<string, string>) => ({
      ...context,
      [extras.hub_feature]: hub,
      [extras.offset_x_feature]: offset.dx,
      [extras.offset_y_feature]: offset.dy,
      ...combo,
    }),
    [context, extras, hub],
  );

  // ── the ETA landscape: one batched call per hub/options/context ───────────────
  const landscapeKey = JSON.stringify([hub, choice, context]);
  useEffect(() => {
    let cancelled = false;
    const n = extras.grid_size;
    const xs = Array.from({ length: n }, (_, i) => offsetBounds.x[0] + ((offsetBounds.x[1] - offsetBounds.x[0]) * i) / (n - 1));
    const ys = Array.from({ length: n }, (_, j) => offsetBounds.y[1] - ((offsetBounds.y[1] - offsetBounds.y[0]) * j) / (n - 1));
    const records = ys.flatMap((dy) => xs.map((dx) => baseRecord({ dx: Math.round(dx), dy: Math.round(dy) }, choice)));
    setScoring(true);
    predict(config.predictionUrl, scenario.slug, records, accessToken, { explain: false })
      .then((response) => {
        if (cancelled) return;
        const p = response.predictions;
        const hasInterval = p.every((r) => r.prediction_lower !== null && r.prediction_upper !== null);
        setLandscape({
          n,
          xs,
          ys,
          values: p.map((r) => r.prediction),
          lower: hasInterval ? p.map((r) => r.prediction_lower as number) : null,
          upper: hasInterval ? p.map((r) => r.prediction_upper as number) : null,
        });
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setScoring(false));
    return () => {
      cancelled = true;
    };
    // landscapeKey captures hub/choice/context.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [landscapeKey, scenario.slug, accessToken, extras.grid_size, offsetBounds]);

  // ── the race: every combination of the choices to the pinned destination ─────
  const combos = useMemo(() => cartesian(extras.choice_features, choiceOptions), [extras.choice_features, choiceOptions]);
  const raceKey = JSON.stringify([hub, pin, context]);
  useEffect(() => {
    let cancelled = false;
    predict(config.predictionUrl, scenario.slug, combos.map((c) => baseRecord(pin, c)), accessToken, { explain: false })
      .then((response) => {
        if (cancelled) return;
        setRace(
          response.predictions.map((p, i) => ({ combo: combos[i], days: p.prediction, lower: p.prediction_lower, upper: p.prediction_upper })),
        );
        setRaceRun((r) => r + 1);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
    // raceKey captures hub/pin/context.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [raceKey, combos, scenario.slug, accessToken]);

  // Start each race from the line: render at 0, then let the CSS transitions run.
  useEffect(() => {
    setRaceStarted(false);
    if (reducedMotion) {
      setRaceStarted(true);
      return;
    }
    const id = requestAnimationFrame(() => requestAnimationFrame(() => setRaceStarted(true)));
    return () => cancelAnimationFrame(id);
  }, [raceRun, reducedMotion]);

  const fastest = useMemo(() => (race ? [...race].sort((a, b) => a.days - b.days)[0] : null), [race]);
  const slowest = useMemo(() => (race ? [...race].sort((a, b) => b.days - a.days)[0] : null), [race]);
  const safest = useMemo(
    () => (race && race.every((r) => r.upper !== null) ? [...race].sort((a, b) => (a.upper as number) - (b.upper as number))[0] : null),
    [race],
  );
  const current = useMemo(
    () => race?.find((r) => extras.choice_features.every((f) => r.combo[f] === choice[f])) ?? null,
    [race, choice, extras.choice_features],
  );
  const axisMax = useMemo(() => Math.ceil(Math.max(1, ...(race ?? []).map((r) => r.upper ?? r.days)) + 1), [race]);
  const ranked = useMemo(() => {
    if (!race) return [];
    const byTime = [...race].sort((a, b) => a.days - b.days);
    const rank = new Map(byTime.map((r, i) => [r, i + 1]));
    const ordered = raceOrder === "fastest" ? byTime : race;
    return ordered.map((r) => ({ ...r, rank: rank.get(r)! }));
  }, [race, raceOrder]);
  const comboLabel = (combo: Record<string, string>) => extras.choice_features.map((f) => combo[f]).join(" · ");
  const firstChoice = extras.choice_features[0];
  const laneColor = (combo: Record<string, string>) => {
    const i = choiceOptions[firstChoice].indexOf(combo[firstChoice]);
    return CATEGORICAL[mode][i % CATEGORICAL[mode].length];
  };

  // ── canvases ─────────────────────────────────────────────────────────────────
  const mapCanvas = useRef<HTMLCanvasElement>(null);
  const mapScene = useRef<HubMapScene | null>(null);
  const landCanvas = useRef<HTMLCanvasElement>(null);
  const landScene = useRef<LandscapeScene | null>(null);
  const [mapBox, mapSize] = useElementSize<HTMLDivElement>();
  const [landBox, landSize] = useElementSize<HTMLDivElement>();

  useEffect(() => {
    if (!mapCanvas.current) return;
    const scene = new HubMapScene(mapCanvas.current, setHub);
    mapScene.current = scene;
    return () => scene.destroy();
  }, [loaded]);
  useEffect(() => {
    if (!landCanvas.current) return;
    const scene = new LandscapeScene(landCanvas.current, setReading, setPin);
    landScene.current = scene;
    return () => scene.destroy();
  }, [loaded]);
  useEffect(() => mapScene.current?.resize(mapSize.width, mapSize.height), [mapSize, loaded]);
  useEffect(() => landScene.current?.resize(landSize.width, landSize.height), [landSize, loaded]);
  useEffect(() => {
    if (geometry && mapScene.current) mapScene.current.setData(geometry, hubPoints, flights);
  }, [geometry, hubPoints, flights, loaded]);

  const domain = useMemo<[number, number]>(() => loaded?.domain ?? [0, 30], [loaded]);
  const pinDays = current?.days ?? null;
  useEffect(() => {
    mapScene.current?.setView({
      mode,
      colors,
      selected: hub,
      pin,
      pinDays,
      pinGlyph: glyphFor(choice),
      daysDomain: domain,
      offsetMax: offsetBounds.max,
      reducedMotion,
    });
  }, [mode, colors, hub, pin, pinDays, choice, glyphFor, domain, offsetBounds.max, reducedMotion, loaded]);
  const hubDots = useMemo(
    () => (showDots ? flights.filter((f) => f.hub === hub).map((f) => ({ dx: f.dx, dy: f.dy, days: f.days })) : []),
    [flights, hub, showDots],
  );
  useEffect(() => {
    landScene.current?.setView({ mode, colors, daysDomain: domain, dots: hubDots, pin, hubLabel: hubLabel(hub), units: extras.offset_units, reducedMotion });
  }, [mode, colors, domain, hubDots, pin, hub, hubLabel, extras.offset_units, reducedMotion, loaded]);
  useEffect(() => {
    // `loaded`: the landscape can arrive while the loading state still hides the canvas.
    if (landscape) landScene.current?.setLandscape(landscape);
  }, [landscape, loaded]);

  const selectedHub = hubPoints.find((h) => h.key === hub);
  const pinReading = landscape ? landScene.current?.read(pin) ?? null : null;

  useCopilotReadable({
    description: `${scenario.title} dispatch tower: the ${noun} ETA model applied to one hub — its predicted-duration landscape for the chosen options, and a race of every option combination to a pinned destination. Use it to answer which option is fastest or safest from this hub, and what delivery date to promise.`,
    value: race
      ? {
          hub: hubLabel(hub),
          pinned_destination_offset: pin,
          context,
          landscape_options: choice,
          options_ranked: [...race]
            .sort((a, b) => a.days - b.days)
            .map((r) => ({ option: comboLabel(r.combo), predicted: Number(r.days.toFixed(1)), interval_90: r.lower !== null ? [Number(r.lower.toFixed(1)), Number((r.upper as number).toFixed(1))] : null })),
          units,
        }
      : null,
  });

  if (error) return <div className="dt-empty">Could not load the dispatch tower: {error}</div>;
  if (!loaded) {
    return (
      <div className="dt-empty dt-loading">
        <span className="dt-spinner" /> Loading every {noun}…
      </div>
    );
  }

  const gradientStops = Array.from({ length: 9 }, (_, i) => `${sequentialColor(i / 8, mode)} ${(i / 8) * 100}%`).join(", ");
  const saving = fastest && slowest ? slowest.days - fastest.days : null;

  return (
    <div className={`dt dt--${mode}`}>
      <header className="dt-head">
        <div>
          <h3>{extras.title}</h3>
          {extras.subtitle && <p>{extras.subtitle}</p>}
        </div>
        <div className="dt-model">
          {loaded.r2 !== null && (
            <span title="Share of the variance in real durations the model explains on shipments it never saw (1.0 = perfect).">
              hold-out R² <b>{loaded.r2.toFixed(2)}</b>
            </span>
          )}
          <span title="Every estimate carries a 90% prediction interval from two quantile models (5th-95th percentile).">
            90% intervals <b>on</b>
          </span>
        </div>
      </header>

      <div className="dt-controls">
        <div className="dt-control">
          <span className="dt-control-label">{featureLabel(scenario, extras.hub_feature)}</span>
          <div className="dt-chips" role="radiogroup">
            {hubPoints.map((h) => (
              <button key={h.key} type="button" role="radio" aria-checked={hub === h.key} className={hub === h.key ? "dt-chip dt-chip--on" : "dt-chip"} onClick={() => setHub(h.key)}>
                {h.label}
              </button>
            ))}
          </div>
        </div>
        {extras.choice_features.map((f) => (
          <div className="dt-control" key={f}>
            <span className="dt-control-label">{featureLabel(scenario, f)}</span>
            <div className="dt-seg" role="radiogroup">
              {choiceOptions[f].map((o) => {
                const g = extras.choice_icons[o];
                return (
                  <button key={o} type="button" role="radio" aria-checked={choice[f] === o} className={choice[f] === o ? "dt-seg--on" : ""} onClick={() => setChoice((c) => ({ ...c, [f]: o }))}>
                    {g && <VehicleIcon glyph={g} size={13} />}
                    {o}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
        {contextFeatures.map((f) => {
          const spec = schema[f];
          return (
            <label className="dt-control" key={f}>
              <span className="dt-control-label">{featureLabel(scenario, f)}</span>
              {spec?.type === "categorical" ? (
                <select value={String(context[f])} onChange={(e) => setContext((c) => ({ ...c, [f]: e.target.value }))}>
                  {spec.options.map((o) => (
                    <option key={o}>{o}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="number"
                  value={Number(context[f])}
                  min={spec?.type === "numeric" ? spec.min : undefined}
                  max={spec?.type === "numeric" ? spec.max : undefined}
                  step={spec?.type === "numeric" ? spec.step : undefined}
                  onChange={(e) => setContext((c) => ({ ...c, [f]: Number(e.target.value) }))}
                />
              )}
            </label>
          );
        })}
      </div>

      <div className="dt-stage">
        <section className="dt-panel dt-map">
          <div className="dt-panel-head">
            <h4>Network</h4>
            <span>
              {flights.length.toLocaleString()} real {noun}s · {selectedHub?.count.toLocaleString()} from {hubLabel(hub)}, median {fmtDays(selectedHub?.medianDays, 0)}
            </span>
          </div>
          <div className="dt-canvas-box" ref={mapBox}>
            <canvas ref={mapCanvas} aria-label={`Map of the ${extras.hubs.length} hubs with ${noun}s in flight`} />
          </div>
          <p className="dt-note">
            Each streak is a real {noun}, coloured by how long it actually took. Destinations are drawn at their {extras.offset_units} offset from the hub — a
            schematic, not a geographic route. Click a hub to focus it.
          </p>
        </section>

        <section className="dt-panel dt-land">
          <div className="dt-panel-head">
            <h4>
              ETA landscape · {hubLabel(hub)} · {extras.choice_features.map((f) => choice[f]).join(" · ")}
            </h4>
            <label className="dt-toggle">
              <input type="checkbox" checked={showDots} onChange={(e) => setShowDots(e.target.checked)} /> real {noun}s
            </label>
          </div>
          <div className="dt-canvas-box dt-canvas-box--land" ref={landBox}>
            <canvas ref={landCanvas} aria-label={`Predicted ${scenario.target_label ?? "duration"} for destinations around ${hubLabel(hub)}`} />
            {scoring && <span className="dt-scoring">scoring {(extras.grid_size ** 2).toLocaleString()} destinations…</span>}
            {reading && (
              <div className="dt-tip" style={{ left: reading.x, top: reading.y }}>
                <b>{fmtDays(reading.days)}</b>
                {reading.lower !== null && (
                  <span>
                    90%: {fmtDays(reading.lower)} – {fmtDays(reading.upper)}
                  </span>
                )}
                <span>
                  {Math.round(reading.dx)} E · {Math.round(reading.dy)} N — click to pin
                </span>
              </div>
            )}
          </div>
          <div className="dt-legend">
            <span>{domain[0]} d</span>
            <span className="dt-legend-bar" style={{ background: `linear-gradient(90deg, ${gradientStops})` }} />
            <span>{domain[1]}+ d</span>
            <em>predicted {scenario.target_label?.toLowerCase() ?? "duration"} · fixed scale across hubs and options</em>
          </div>
        </section>
      </div>

      <section className="dt-panel dt-race">
        <div className="dt-panel-head">
          <h4>
            The race · {hubLabel(hub)} → pinned destination ({pin.dx} E, {pin.dy} N)
            {pinReading && <em> · {fmtDays(pinReading.days)} with your options</em>}
          </h4>
          <div className="dt-race-actions">
            <div className="dt-seg" role="radiogroup" aria-label="Lane order">
              <button type="button" className={raceOrder === "grouped" ? "dt-seg--on" : ""} onClick={() => setRaceOrder("grouped")}>
                By {featureLabel(scenario, firstChoice).toLowerCase()}
              </button>
              <button type="button" className={raceOrder === "fastest" ? "dt-seg--on" : ""} onClick={() => setRaceOrder("fastest")}>
                Fastest first
              </button>
            </div>
            <button type="button" className="dt-replay" onClick={() => setRaceRun((r) => r + 1)}>
              ↻ Replay
            </button>
          </div>
        </div>

        {race && fastest && (
          <div className="dt-verdicts">
            <div className="dt-verdict dt-verdict--fast">
              <span className="dt-verdict-label">Fastest</span>
              <span className="dt-verdict-value">
                <VehicleIcon glyph={glyphFor(fastest.combo)} size={18} /> {comboLabel(fastest.combo)}
              </span>
              <span className="dt-verdict-num">{fmtDays(fastest.days)}</span>
            </div>
            {safest && (
              <div className="dt-verdict dt-verdict--safe">
                <span className="dt-verdict-label">Safest promise</span>
                <span className="dt-verdict-value">
                  <VehicleIcon glyph={glyphFor(safest.combo)} size={18} /> {comboLabel(safest.combo)}
                </span>
                <span className="dt-verdict-num">promise {Math.ceil(safest.upper as number)} {units}</span>
                <span className="dt-verdict-note">upper end of its 90% interval — about 19 in 20 similar {noun}s arrive by then</span>
              </div>
            )}
            {saving !== null && (
              <div className="dt-verdict">
                <span className="dt-verdict-label">Choosing well is worth</span>
                <span className="dt-verdict-num">{fmtDays(saving)}</span>
                <span className="dt-verdict-note">
                  vs. the slowest option ({comboLabel(slowest!.combo)}, {fmtDays(slowest!.days)})
                </span>
              </div>
            )}
          </div>
        )}

        <div className="dt-lanes" style={{ ["--axis-max" as string]: axisMax }}>
          <div className="dt-axis">
            {Array.from({ length: Math.floor(axisMax / 2) + 1 }, (_, i) => i * 2).map((d) => (
              <span key={d} style={{ left: `${(d / axisMax) * 100}%` }}>
                {d}
              </span>
            ))}
          </div>
          {ranked.map((entry, i) => {
            const color = laneColor(entry.combo);
            const pos = (entry.days / axisMax) * 100;
            const duration = reducedMotion ? 0 : 0.7 + 2.6 * (entry.days / axisMax);
            const newGroup = raceOrder === "grouped" && extras.choice_features.length > 1 && (i === 0 || ranked[i - 1].combo[firstChoice] !== entry.combo[firstChoice]);
            const isCurrent = entry === current || extras.choice_features.every((f) => entry.combo[f] === choice[f]);
            return (
              <div key={`${raceRun}-${comboLabel(entry.combo)}`} className={`dt-lane${newGroup ? " dt-lane--group" : ""}${isCurrent ? " dt-lane--current" : ""}`} style={{ ["--lane" as string]: color }}>
                <span className="dt-lane-label" title={comboLabel(entry.combo)}>
                  <span className="dt-lane-swatch" />
                  {comboLabel(entry.combo)}
                </span>
                <span className="dt-track">
                  {entry.lower !== null && entry.upper !== null && (
                    <span
                      className="dt-band"
                      style={{ left: `${(entry.lower / axisMax) * 100}%`, width: `${((entry.upper - entry.lower) / axisMax) * 100}%`, opacity: raceStarted ? 1 : 0, transitionDelay: `${duration}s` }}
                      title={`90% interval ${fmtDays(entry.lower)} – ${fmtDays(entry.upper)}`}
                    />
                  )}
                  <span className="dt-trail" style={{ width: raceStarted ? `${pos}%` : "0%", transitionDuration: `${duration}s` }} />
                  <span className="dt-vehicle" style={{ left: raceStarted ? `${pos}%` : "0%", transitionDuration: `${duration}s` }}>
                    <VehicleIcon glyph={glyphFor(entry.combo)} size={16} />
                  </span>
                </span>
                <span className="dt-lane-result" style={{ opacity: raceStarted ? 1 : 0, transitionDelay: `${duration}s` }}>
                  <span className={`dt-rank${entry.rank === 1 ? " dt-rank--first" : ""}`}>{entry.rank === 1 ? "🏁 1st" : `#${entry.rank}`}</span>
                  <b>{fmtDays(entry.days)}</b>
                  {entry.lower !== null && (
                    <em>
                      {entry.lower.toFixed(1)}–{(entry.upper as number).toFixed(1)}
                    </em>
                  )}
                </span>
              </div>
            );
          })}
          <p className="dt-note">
            {units[0].toUpperCase() + units.slice(1)} after dispatch · bar = predicted, shaded band = 90% prediction interval · the highlighted lane is
            the option shown on the landscape. Colour = {featureLabel(scenario, firstChoice).toLowerCase()}.
          </p>
        </div>
      </section>
    </div>
  );
}
