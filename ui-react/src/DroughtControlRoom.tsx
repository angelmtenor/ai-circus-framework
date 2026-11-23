/**
 * The drought control room: the forecast board of a scenario with `analysis_toolset: drought_watch`
 * (docs/adr/0030). Everything it shows comes from the assistant's read-only `/drought/{slug}/…` routes — the same
 * toolkit the chat's tools call, so the tab and the assistant never disagree — plus prediction's stored forecast.
 *
 * - each supply system's state against the plan, now or on any week since 2000 (the replay);
 * - the basin map coloured by each reservoir's storage band, and the selected series on a chart with the plan's
 *   thresholds behind it;
 * - a what-if (rainfall scenario, demand savings, horizon) and the least restrictive package of measures;
 * - what drives the forecast, the data-quality flags (readings as published next to the cleaned series), the plan
 *   and the weekly bulletin.
 *
 * DOM and SVG only. The replay advances on a bounded timer only while the user plays it, then stops.
 */
import { useEffect, useMemo, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import {
  droughtBulletin,
  droughtControlRoom,
  droughtHistory,
  droughtOptimise,
  droughtQuality,
  droughtQualitySeries,
  droughtWhatIf,
  fetchForecast,
  type DroughtControlRoom as Room,
  type DroughtHistory,
  type DroughtOptimise,
  type DroughtQualityOverview,
  type DroughtQualitySeries,
  type DroughtScenario,
  type DroughtStateSpec,
  type DroughtWhatIf,
  type Forecast,
  type ForecastBoardExtra,
  type ScenarioSummary,
} from "./apiClient";
import { config } from "./config";
import { BarList, CHART_COLORS } from "./charts";
import { SeriesMap, TimeChart, type TimeLine, type TimeZone } from "./ForecastViews";
import { seriesLines } from "./forecastLogic";
import { NumberSlider } from "./controls";
import { Icon } from "./Icon";
import { renderMarkdown } from "./markdown";
import "./forecast.css";
import "./droughtControlRoom.css";

// The plan's states, best to worst: teal, amber, orange, red, magenta.
const STATE_COLORS: Record<string, string> = {
  normal: "#22d3c8",
  pre_alert: "#ffcf4d",
  alert: "#ff9b4a",
  exceptional: "#ff5f7a",
  emergency: "#d23cff",
};
const stateColor = (id: string | null | undefined) => (id ? (STATE_COLORS[id] ?? CHART_COLORS.dim) : CHART_COLORS.dim);
const REPLAY_STEP = 4; // weeks per tick: 27 years replay in about 50 seconds
const REPLAY_TICK_MS = 140;
const HORIZONS = [30, 90, 180];
const SCENARIOS: { id: DroughtScenario; label: string }[] = [
  { id: "dry", label: "Dry" },
  { id: "normal", label: "Normal" },
  { id: "wet", label: "Wet" },
];
const DRIVER_LABELS: Record<string, string> = { base: "Starting point (model average)" };
const CODE_LABELS: Record<string, string> = {
  duplicate: "Duplicate day",
  gap: "Gap",
  flatline: "Frozen sensor",
  jump: "Sudden jump",
  pct_range: "Above 100 % or below 0",
  level_volume: "Level-volume mismatch",
};

const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : `${v.toFixed(digits)} %`);
const signed = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}`;
const dateText = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });

/** A storage value's band in the plan (a reservoir has no state of its own: this is its storage against the thresholds). */
function bandOf(states: DroughtStateSpec[], value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  for (const s of states) if (s.min_pct === null || value >= s.min_pct) return s.id;
  return states[states.length - 1].id;
}

function zonesOf(states: DroughtStateSpec[]): TimeZone[] {
  return states.map((s, i) => ({
    key: s.id,
    from: s.min_pct ?? 0,
    to: i === 0 ? 110 : (states[i - 1].min_pct ?? 110),
    color: stateColor(s.id),
    label: s.label,
  }));
}

function StateChip({ id, states }: { id: string | null | undefined; states: DroughtStateSpec[] }) {
  const label = states.find((s) => s.id === id)?.label ?? "—";
  return (
    <span className="drought-chip" style={{ ["--state" as string]: stateColor(id) }}>
      {label}
    </span>
  );
}

function Segmented<T extends string | number>({ options, value, onChange, label }: { options: { id: T; label: string }[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div className="drought-segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={String(o.id)} className={o.id === value ? "active" : ""} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function DroughtControlRoom({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as ForecastBoardExtra;
  const base = config.assistantUrl;
  const [room, setRoom] = useState<Room | null>(null);
  const [history, setHistory] = useState<DroughtHistory | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState("central");
  // An index into the weekly history while replaying; null shows today with the forecast.
  const [at, setAt] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [view, setView] = useState<"forecast" | "history">("forecast");

  useEffect(() => {
    let live = true;
    Promise.all([droughtControlRoom(base, scenario.slug, accessToken), fetchForecast(config.predictionUrl, scenario.slug, accessToken)])
      .then(([r, f]) => {
        if (!live) return;
        setRoom(r);
        setForecast(f);
      })
      .catch((e: Error) => live && setError(e.message));
    droughtHistory(base, scenario.slug, accessToken)
      .then((h) => live && setHistory(h))
      .catch(() => undefined); // the replay is optional: the room works without it
    return () => {
      live = false;
    };
  }, [base, scenario.slug, accessToken]);

  const last = history ? history.dates.length - 1 : 0;
  const running = playing && history !== null && at !== null && at < last;
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setAt((i) => Math.min((i ?? 0) + REPLAY_STEP, last)), REPLAY_TICK_MS);
    return () => window.clearInterval(id);
  }, [running, last]);

  const systemOf = useMemo(() => new Map((room?.reservoirs ?? []).map((r) => [r.id, r.system])), [room]);
  const systemId = room?.systems.some((s) => s.id === selected) ? selected : (systemOf.get(selected) ?? "central");
  const names = useMemo(
    () => new Map<string, string>([...(room?.systems ?? []).map((s) => [s.id, s.name] as const), ...(room?.reservoirs ?? []).map((r) => [r.id, r.name] as const)]),
    [room],
  );

  useCopilotReadable({
    description: `The ${scenario.title} drought control room: the selected series, the date shown (null = today) and each system's state on it.`,
    value: room ? { selected, date: at === null ? room.as_of : history?.dates[at], systems: room.systems.map((s) => ({ id: s.id, state: s.state, pct: s.current_pct })) } : null,
  });

  if (error) return <div className="tab-panel"><div className="error">Could not load the control room: {error}</div></div>;
  if (!room || !forecast) return <div className="tab-panel"><div className="panel-hint">Loading the control room…</div></div>;

  const replaying = at !== null && history !== null;
  const shownDate = replaying ? history!.dates[at!] : room.as_of;
  const valueOn = (series: string): number | null =>
    replaying
      ? (history!.series[series]?.[at!] ?? null)
      : (room.systems.find((s) => s.id === series)?.current_pct ?? room.reservoirs.find((r) => r.id === series)?.current_pct ?? null);
  const systemStateOn = (sid: string) => (replaying ? (history!.states[sid]?.[at!] ?? null) : (room.systems.find((s) => s.id === sid)?.state ?? null));
  const forecastIndex = forecast.series.findIndex((s) => s.label === selected);

  function startReplay() {
    if (!history) return;
    setView("history");
    setAt((i) => (i === null || i >= last ? 0 : i));
    setPlaying(true);
  }
  function jumpTo(year: string) {
    if (!history) return;
    const i = history.dates.findIndex((d) => d >= year);
    setPlaying(false);
    setView("history");
    setAt(i < 0 ? last : i);
  }
  function backToToday() {
    setPlaying(false);
    setAt(null);
    setView("forecast");
  }

  return (
    <div className="tab-panel forecast drought-room">
      <header className="drought-head">
        <div>
          <h2>{extras.title}</h2>
          {extras.subtitle && <p className="panel-hint">{extras.subtitle}</p>}
        </div>
        <div className="drought-asof">
          <span className="drought-asof-label">{replaying ? "Replaying" : "Latest readings"}</span>
          <span className="drought-asof-date">{dateText(shownDate)}</span>
          {replaying && (
            <button className="btn-secondary" onClick={backToToday}>
              Back to today
            </button>
          )}
        </div>
      </header>

      <section className="drought-systems" aria-label="Supply systems">
        {room.systems.map((s) => {
          const state = systemStateOn(s.id);
          const value = valueOn(s.id);
          const p50 = (h: number) => s.forecast_states[String(h)]?.p50;
          return (
            <button
              key={s.id}
              className={`drought-system${systemId === s.id ? " drought-system--on" : ""}`}
              style={{ ["--state" as string]: stateColor(state) }}
              onClick={() => setSelected(s.id)}
            >
              <span className="drought-system-name">{s.name}</span>
              <span className="drought-system-value">{pct(value)}</span>
              <StateChip id={state} states={room.states} />
              {!replaying ? (
                <span className="drought-system-meta">
                  {signed(s.change_30d_pp)} pp in 30 days · {s.days_in_state} days in state
                </span>
              ) : (
                <span className="drought-system-meta">State on {dateText(shownDate)}</span>
              )}
              {!replaying && (
                <span className="drought-system-ahead" title="The median forecast's state at 30, 90 and 180 days">
                  {HORIZONS.map((h) => (
                    <span key={h} style={{ ["--state" as string]: stateColor(p50(h)) }}>
                      <i /> {h} d
                    </span>
                  ))}
                </span>
              )}
            </button>
          );
        })}
      </section>

      <div className="forecast-board-body with-map">
        <div className="panel-card drought-map-card">
          {extras.map && (
            <SeriesMap
              map={extras.map}
              forecast={forecast}
              selected={selected}
              labels={extras.series_labels}
              onSelect={(i) => setSelected(forecast.series[i].label)}
              colorOf={(series) => stateColor(bandOf(room.states, valueOn(series)))}
            />
          )}
          <div className="drought-legend" aria-label="The plan's states">
            {room.states.map((s) => (
              <span key={s.id} style={{ ["--state" as string]: stateColor(s.id) }}>
                <i /> {s.label} <small>{s.min_pct === null ? `< ${room.states[room.states.length - 2].min_pct} %` : `≥ ${s.min_pct} %`}</small>
              </span>
            ))}
          </div>
          {history && (
            <div className="drought-replay">
              <div className="drought-replay-head">
                <span>Replay {history.dates[0].slice(0, 4)}–{room.as_of.slice(0, 4)}</span>
                <span className="drought-replay-buttons">
                  <button className="btn-secondary" onClick={() => (running ? setPlaying(false) : startReplay())}>
                    <Icon name={running ? "pause" : "play"} size={12} /> {running ? "Pause" : "Play"}
                  </button>
                  <button className="btn-secondary" onClick={() => jumpTo("2022-06")}>2022–24 drought</button>
                </span>
              </div>
              <input
                type="range"
                aria-label="Week shown"
                min={0}
                max={last}
                value={at ?? last}
                onChange={(e) => {
                  const i = Number(e.target.value);
                  setPlaying(false);
                  setView("history");
                  setAt(i === last ? null : i);
                }}
              />
            </div>
          )}
        </div>

        <div className="panel-card">
          <div className="forecast-head">
            <h3>{names.get(selected) ?? selected}</h3>
            <Segmented
              label="Chart"
              options={[
                { id: "forecast" as const, label: "Next 180 days" },
                { id: "history" as const, label: "Since 2000" },
              ]}
              value={view}
              onChange={(v) => {
                setView(v);
                if (v === "forecast") backToToday();
              }}
            />
          </div>
          <SeriesChart room={room} forecast={forecast} forecastIndex={forecastIndex} history={history} selected={selected} view={view} at={at} />
          <p className="panel-hint">
            Behind the line, the plan's states. A supply system's state also needs {room.hysteresis_days} days below a threshold before it
            changes; a reservoir is shown against the same thresholds for reference.
          </p>
        </div>
      </div>

      <Outlook room={room} systemId={systemId} slug={scenario.slug} accessToken={accessToken} />
      <Drivers room={room} selected={selected} name={names.get(selected) ?? selected} />
      <Quality room={room} slug={scenario.slug} accessToken={accessToken} />
      <Plan room={room} />
      <Bulletin slug={scenario.slug} accessToken={accessToken} />
    </div>
  );
}

function SeriesChart({
  room,
  forecast,
  forecastIndex,
  history,
  selected,
  view,
  at,
}: {
  room: Room;
  forecast: Forecast;
  forecastIndex: number;
  history: DroughtHistory | null;
  selected: string;
  view: "forecast" | "history";
  at: number | null;
}) {
  const zones = useMemo(() => zonesOf(room.states), [room.states]);
  const historyLines = useMemo<TimeLine[]>(() => {
    if (!history) return [];
    const values = history.series[selected] ?? [];
    const ds: string[] = [];
    const vs: number[] = [];
    values.forEach((v, i) => {
      if (v !== null) {
        ds.push(history.dates[i]);
        vs.push(v);
      }
    });
    return [{ key: "weekly", label: "Stored (weekly)", ds, values: vs, color: CHART_COLORS.accent, width: 1.5 }];
  }, [history, selected]);
  if (view === "history" && history) {
    const marker = at === null ? undefined : { ds: history.dates[at], label: dateText(history.dates[at]) };
    return <TimeChart lines={historyLines} zones={zones} yDomain={[0, 110]} frequency="W" yLabel="Stored water (% of capacity)" marker={marker} height={300} />;
  }
  const series = forecast.series[forecastIndex];
  if (!series) return <p className="panel-hint">No forecast for this series.</p>;
  const { lines, bands } = seriesLines(series, forecast.levels, "Forecast (median)");
  // A reservoir holds between empty and full: a quantile model can step past 100 %, the tools and bulletin clip it too.
  const capped = (values: number[]) => values.map((v) => Math.min(Math.max(v, 0), 100));
  return (
    <TimeChart
      lines={lines.map((l) => ({ ...l, values: capped(l.values) }))}
      bands={bands.map((b) => ({ ...b, lower: capped(b.lower), upper: capped(b.upper) }))}
      zones={zones}
      yDomain={[0, 110]}
      frequency={forecast.frequency}
      yLabel="Stored water (% of capacity)"
      marker={{ ds: series.history.ds[series.history.ds.length - 1], label: "today" }}
      height={300}
    />
  );
}

/** What-if and measures for one supply system, through the toolkit's what_if and optimise_measures. */
function Outlook({ room, systemId, slug, accessToken }: { room: Room; systemId: string; slug: string; accessToken: string | null }) {
  const system = room.systems.find((s) => s.id === systemId)!;
  const [scenario, setScenario] = useState<DroughtScenario>("dry");
  const [savings, setSavings] = useState(0);
  const [horizon, setHorizon] = useState(180);
  const [result, setResult] = useState<DroughtWhatIf | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<DroughtOptimise | null>(null);
  const [planning, setPlanning] = useState(false);

  useEffect(() => {
    let live = true;
    // Debounced: a slider drag fires many changes, one what-if is enough.
    const timer = window.setTimeout(() => {
      droughtWhatIf(config.assistantUrl, slug, { system: systemId, scenario, savings_pct: savings, horizon }, accessToken)
        .then((r) => {
          if (!live) return;
          setResult(r);
          setError(null);
        })
        .catch((e: Error) => live && setError(e.message));
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [slug, systemId, scenario, savings, horizon, accessToken]);

  async function optimise() {
    setPlanning(true);
    try {
      setPlan(await droughtOptimise(config.assistantUrl, slug, { system: systemId, target_probability_pct: 10, horizon, scenario }, accessToken));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPlanning(false);
    }
  }

  const shownPlan = plan && plan.system === systemId && plan.horizon_days === horizon && plan.scenario === scenario ? plan : null;
  return (
    <section className="panel-card drought-outlook">
      <div className="forecast-head">
        <h3>Outlook and measures · {system.name}</h3>
        <span className="panel-hint">Pick a system above to change it.</span>
      </div>
      <div className="drought-outlook-controls">
        <label>
          <span>Rainfall from now</span>
          <Segmented label="Rainfall scenario" options={SCENARIOS} value={scenario} onChange={setScenario} />
        </label>
        <label>
          <span>Looking ahead</span>
          <Segmented label="Horizon" options={HORIZONS.map((h) => ({ id: h, label: `${h} days` }))} value={horizon} onChange={setHorizon} />
        </label>
        <div className="drought-savings">
          <NumberSlider label="Demand savings" min={0} max={60} step={5} value={savings} unit="%" editable={false} onChange={setSavings} />
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {result && (
        <>
          <div className="drought-cases">
            {(["p10", "p50", "p90"] as const).map((q) => (
              <div key={q} className={`drought-case${q === "p50" ? " drought-case--main" : ""}`} style={{ ["--state" as string]: stateColor(result.end_of_horizon_state[q]) }}>
                <span className="drought-case-label">{q === "p10" ? "Bad case (1 in 10)" : q === "p50" ? "Most likely" : "Good case (1 in 10)"}</span>
                <span className="drought-case-value">{pct(result.end_of_horizon_pct[q])}</span>
                <StateChip id={result.end_of_horizon_state[q]} states={room.states} />
              </div>
            ))}
          </div>
          <p className="panel-hint">In {horizon} days, from {pct(result.current_pct)} today.</p>
          <table className="data-table drought-thresholds">
            <thead>
              <tr>
                <th>Falling below</th>
                <th>Chance within {horizon} days</th>
                <th>When, if it happens</th>
              </tr>
            </thead>
            <tbody>
              {result.thresholds.map((t) => (
                <tr key={t.state_below}>
                  <td>
                    <StateChip id={t.state_below} states={room.states} /> {t.threshold_pct} %
                  </td>
                  <td>
                    <span className="drought-prob">
                      <span style={{ width: `${Math.max(1, t.probability_below_within_horizon * 100)}%`, background: stateColor(t.state_below) }} />
                    </span>{" "}
                    {(t.probability_below_within_horizon * 100).toFixed(0)} %
                  </td>
                  <td>
                    {t.days_pessimistic_p10 === null ? "Not within the horizon" : `from day ${Math.round(t.days_pessimistic_p10)} in the bad case`}
                    {t.days_median_p50 !== null && ` · day ${Math.round(t.days_median_p50)} most likely`}
                  </td>
                </tr>
              ))}
              {result.thresholds.length === 0 && (
                <tr>
                  <td colSpan={3}>Already in the worst state.</td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}
      <div className="drought-optimise">
        <button className="btn-primary" onClick={optimise} disabled={planning}>
          <Icon name="sparkle" size={14} /> {planning ? "Comparing packages…" : `Least restrictive package that keeps the chance of emergency at or below 10 %`}
        </button>
        {shownPlan && (
          <div className="drought-packages">
            {shownPlan.packages.map((p) => (
              <div key={p.package} className={`drought-package${p.package === shownPlan.least_restrictive_package ? " drought-package--chosen" : ""}`}>
                <StateChip id={p.package} states={room.states} />
                <span className="drought-package-savings">{p.savings_pct} % savings</span>
                <span className="drought-prob drought-prob--wide">
                  <span style={{ width: `${Math.max(1, p.probability_below_pct)}%`, background: stateColor(p.package) }} />
                </span>
                <span className="drought-package-p">{p.probability_below_pct.toFixed(0)} % chance of emergency</span>
              </div>
            ))}
            <p className="panel-hint">{shownPlan.note}</p>
          </div>
        )}
      </div>
      <p className="panel-hint">{result?.method}</p>
    </section>
  );
}

function Drivers({ room, selected, name }: { room: Room; selected: string; name: string }) {
  const [horizon, setHorizon] = useState(90);
  const drivers = room.drivers[selected]?.[String(horizon)] ?? {};
  const items = Object.entries(drivers).map(([label, value]) => ({ label: DRIVER_LABELS[label] ?? label.charAt(0).toUpperCase() + label.slice(1), value }));
  return (
    <section className="panel-card">
      <div className="forecast-head">
        <h3>Why the forecast moves · {name}</h3>
        <Segmented label="Horizon" options={[7, ...HORIZONS].map((h) => ({ id: h, label: `${h} d` }))} value={horizon} onChange={setHorizon} />
      </div>
      <p className="panel-hint">How much each group of inputs moves the median forecast, in percentage points of capacity (SHAP).</p>
      {items.length > 0 ? (
        <BarList items={items} positiveColor={CHART_COLORS.accent} negativeColor={CHART_COLORS.red} valueFormatter={(v) => `${signed(v)} pp`} />
      ) : (
        <p className="panel-hint">No drivers for this series.</p>
      )}
    </section>
  );
}

function Quality({ room, slug, accessToken }: { room: Room; slug: string; accessToken: string | null }) {
  const [overview, setOverview] = useState<DroughtQualityOverview | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  // null: the latest 120 days; a flag kind: the 120 days around that kind's latest flag.
  const [code, setCode] = useState<string | null>(null);
  const [window_, setWindow] = useState<DroughtQualitySeries | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    droughtQuality(config.assistantUrl, slug, accessToken)
      .then((q) => live && setOverview(q))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [slug, accessToken]);
  useEffect(() => {
    if (!picked) return;
    let live = true;
    droughtQualitySeries(config.assistantUrl, slug, picked, code, accessToken)
      .then((q) => live && setWindow(q))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [picked, code, slug, accessToken]);

  const lines: TimeLine[] = window_
    ? [
        {
          key: "published",
          label: "As published",
          ds: window_.published.filter((p) => p.pct !== null).map((p) => p.day),
          values: window_.published.filter((p) => p.pct !== null).map((p) => p.pct as number),
          color: CHART_COLORS.amber,
          width: 1.5,
        },
        { key: "cleaned", label: "Cleaned (what the model reads)", ds: window_.cleaned.map((p) => p.day), values: window_.cleaned.map((p) => p.pct), color: CHART_COLORS.accent, width: 2 },
      ]
    : [];
  const codes = overview ? [...new Set(overview.reservoirs.flatMap((r) => Object.keys(r.flags_by_code)))] : [];
  return (
    <section className="panel-card">
      <h3>Data quality</h3>
      <p className="panel-hint">
        The feed is published without validation. A flag means "needs review by a person", never "wrong".
        {overview && ` Before training, ${Object.entries(overview.cleaning_applied).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}.`}
      </p>
      {error && <p className="error">{error}</p>}
      {overview && (
        <div className="table-scroll">
          <table className="data-table drought-quality">
            <thead>
              <tr>
                <th>Reservoir</th>
                <th>Score</th>
                <th>Last 30 days</th>
                {codes.map((c) => (
                  <th key={c} title={overview.meaning[c]}>
                    {CODE_LABELS[c] ?? c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {overview.reservoirs.map((r) => (
                <tr key={r.id} className={picked === r.id ? "drought-row--on" : undefined}>
                  <td>
                    <button
                      className="drought-link"
                      onClick={() => {
                        setPicked(r.id);
                        setCode(null);
                      }}
                    >
                      {r.name}
                    </button>
                  </td>
                  <td>{pct(r.score_pct, 0)}</td>
                  <td>{room.reservoirs.find((x) => x.id === r.id)?.flags_30d ?? 0}</td>
                  {codes.map((c) => (
                    <td key={c}>{r.flags_by_code[c] ?? 0}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!picked && overview && <p className="panel-hint">Open a reservoir to see its readings as published next to the cleaned series.</p>}
      {window_ && overview && (
        <div className="drought-quality-window">
          <div className="drought-quality-kinds" role="group" aria-label="Window">
            <button className={code === null ? "active" : ""} onClick={() => setCode(null)}>
              Latest 120 days
            </button>
            {Object.entries(overview.reservoirs.find((r) => r.id === picked)?.flags_by_code ?? {}).map(([c, n]) => (
              <button key={c} className={code === c ? "active" : ""} onClick={() => setCode(c)} title={overview.meaning[c]}>
                {CODE_LABELS[c] ?? c} <small>{n}</small>
              </button>
            ))}
          </div>
          <h4>
            {window_.name}: {dateText(window_.period[0])} – {dateText(window_.period[1])} · {window_.days_flagged} days flagged, {window_.missing_days} missing
          </h4>
          <TimeChart lines={lines} frequency="D" yLabel="% of capacity" height={220} />
          {window_.flags.length > 0 && (
            <ul className="drought-flags">
              {window_.flags.slice(0, 12).map((f) => (
                <li key={`${f.day}-${f.code}`}>
                  <b>{dateText(f.day)}</b> {CODE_LABELS[f.code] ?? f.code}: {window_.meaning[f.code] ?? f.detail} <span className="panel-hint">({f.detail})</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function Plan({ room }: { room: Room }) {
  return (
    <section className="panel-card">
      <h3>The drought plan</h3>
      <p className="panel-hint">{room.plan_title}. Fictional and illustrative: thresholds, cuts and caps are configuration, not decisions.</p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>State</th>
              <th>System storage</th>
              <th>Agriculture</th>
              <th>Industry</th>
              <th>Urban cap</th>
              <th>Estimated savings</th>
            </tr>
          </thead>
          <tbody>
            {room.states.map((s, i) => (
              <tr key={s.id}>
                <td>
                  <StateChip id={s.id} states={room.states} />
                </td>
                <td>{s.min_pct === null ? `below ${room.states[i - 1]?.min_pct} %` : i === 0 ? `${s.min_pct} % or more` : `${s.min_pct}–${room.states[i - 1].min_pct} %`}</td>
                <td>{s.measures.agriculture_pct ? `−${s.measures.agriculture_pct} %` : "—"}</td>
                <td>{s.measures.industry_pct ? `−${s.measures.industry_pct} %` : "—"}</td>
                <td>{s.measures.urban_cap_lpd ? `${s.measures.urban_cap_lpd} l/person/day` : "—"}</td>
                <td>{s.savings_pct ? `${s.savings_pct} %` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Bulletin({ slug, accessToken }: { slug: string; accessToken: string | null }) {
  const [lang, setLang] = useState("en");
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function preview() {
    setBusy(true);
    try {
      setText((await droughtBulletin(config.assistantUrl, slug, lang, accessToken)).markdown);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function pdf() {
    // The route needs the bearer token, so the PDF is fetched and opened as a blob rather than linked.
    setBusy(true);
    try {
      const response = await fetch(`${config.assistantUrl}/bulletin/${slug}?lang=${lang}`, { headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {} });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const url = URL.createObjectURL(await response.blob());
      window.open(url, "_blank", "noopener");
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel-card">
      <div className="forecast-head">
        <h3>Weekly bulletin</h3>
        <span className="drought-bulletin-actions">
          <Segmented label="Language" options={[{ id: "en", label: "English" }, { id: "es", label: "Español" }]} value={lang} onChange={(l) => { setLang(l); setText(null); }} />
          <button className="btn-secondary" onClick={preview} disabled={busy}>
            Preview
          </button>
          <button className="btn-primary" onClick={pdf} disabled={busy}>
            <Icon name="download" size={14} /> PDF
          </button>
        </span>
      </div>
      <p className="panel-hint">Drafted from the same data, plan and forecast; every figure in it comes from a tool. A person reviews it before it is published.</p>
      {error && <p className="error">{error}</p>}
      {text && <div className="drought-bulletin">{renderMarkdown(text)}</div>}
    </section>
  );
}
