/**
 * The workspace of a `model.task_type: forecast` scenario (docs/adr/0016-time-series-forecasting.md):
 * the ML Predictions tab shows the forecast (history, point values, 80/95 % bands, step by step),
 * ML Insights shows how every candidate did on the backtest and what drives the forecast, and the
 * opt-in `forecast_board` tab is a what-if — levers shift the assumed exogenous values and the
 * prediction service re-forecasts. All charts are static SVG (no canvas, so no frame loop) drawn
 * with the theme's CSS variables like charts.tsx.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import {
  fetchForecast,
  fetchForecastBacktest,
  forecastWhatIf,
  modelCard,
  type Forecast,
  type ForecastBacktest,
  type ForecastBoardExtra,
  type ForecastSeries,
  type ModelCard,
  type ScenarioSummary,
} from "./apiClient";
import { config } from "./config";
import { BarList, CHART_COLORS, StatTile } from "./charts";
import "./forecast.css";

const CANDIDATE_LABELS: Record<string, string> = {
  seasonal_naive: "Seasonal naive",
  auto_ets: "Exponential smoothing",
  lightgbm_lags: "LightGBM on lags",
};
const CANDIDATE_COLORS = [CHART_COLORS.amber, CHART_COLORS.purple, CHART_COLORS.accent, CHART_COLORS.green];

const candidateLabel = (name: string) => CANDIDATE_LABELS[name] ?? name;

/** Short time label for the frequency: hours show weekday + time, days and coarser show the date. */
function timeLabel(iso: string, frequency: string): string {
  const d = new Date(iso);
  if (/min|h$/.test(frequency)) return d.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: /M|Q/.test(frequency) ? "numeric" : undefined });
}

const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));

function useForecast(scenario: ScenarioSummary, accessToken: string | null) {
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    fetchForecast(config.predictionUrl, scenario.slug, accessToken)
      .then((f) => live && setForecast(f))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [scenario.slug, accessToken]);
  return { forecast, error };
}

// ── Chart ────────────────────────────────────────────────────────────────────

export type TimeLine = { key: string; label: string; ds: string[]; values: number[]; color: string; dashed?: boolean; width?: number };
export type TimeBand = { key: string; ds: string[]; lower: number[]; upper: number[]; opacity: number; color?: string };

const PAD = { left: 56, right: 16, top: 14, bottom: 34 };

/** Round axis bounds and step for `lo`..`hi`, about four intervals. */
function niceAxis(lo: number, hi: number): { lo: number; hi: number; ticks: number[] } {
  const raw = (hi - lo || 1) / 4;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / magnitude;
  const step = (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * magnitude;
  const low = Math.floor(lo / step) * step;
  const high = Math.ceil(hi / step) * step;
  return { lo: low, hi: high, ticks: Array.from({ length: Math.round((high - low) / step) + 1 }, (_, i) => low + i * step) };
}

/** History, forecast and bands on one time axis, with a hover read-out of every line. */
export function TimeChart({
  lines,
  bands = [],
  frequency,
  yLabel,
  marker,
  height: tallest = 300,
}: {
  lines: TimeLine[];
  bands?: TimeBand[];
  frequency: string;
  yLabel: string;
  // A vertical rule (e.g. where the forecast starts).
  marker?: { ds: string; label: string };
  height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  // Drawn at the container's real width, so the labels stay readable on a phone.
  const [W, setW] = useState(820);
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const observer = new ResizeObserver(([entry]) => setW(Math.max(280, Math.round(entry.contentRect.width))));
    observer.observe(box);
    return () => observer.disconnect();
  }, []);
  const height = W < 520 ? Math.min(tallest, 240) : tallest;
  const times = useMemo(() => {
    const all = new Set<number>();
    for (const l of lines) for (const d of l.ds) all.add(Date.parse(d));
    return [...all].sort((a, b) => a - b);
  }, [lines]);
  if (times.length < 2) return <div className="ts-chart" ref={boxRef} />;
  const values = [...lines.flatMap((l) => l.values), ...bands.flatMap((b) => [...b.lower, ...b.upper])].filter(Number.isFinite);
  const { lo, hi, ticks: yTicks } = niceAxis(Math.min(...values), Math.max(...values));
  const t0 = times[0];
  const t1 = times[times.length - 1];
  const x = (iso: string | number) => PAD.left + ((typeof iso === "number" ? iso : Date.parse(iso)) - t0) / (t1 - t0) * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo)) * (height - PAD.top - PAD.bottom);
  const path = (ds: string[], vs: number[]) => ds.map((d, i) => `${i ? "L" : "M"}${x(d).toFixed(1)},${y(vs[i]).toFixed(1)}`).join("");
  const xTickCount = W < 520 ? 3 : 6;
  const xTicks = Array.from({ length: xTickCount }, (_, i) => times[Math.round(((times.length - 1) * i) / (xTickCount - 1))]);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return;
    const px = ((e.clientX - box.left) / box.width) * W;
    const t = t0 + ((px - PAD.left) / (W - PAD.left - PAD.right)) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < times.length; i++) if (Math.abs(times[i] - t) < Math.abs(times[best] - t)) best = i;
    setHover(times[best]);
  };
  const readout =
    hover === null
      ? []
      : lines.flatMap((l) => {
          const i = l.ds.findIndex((d) => Date.parse(d) === hover);
          return i < 0 ? [] : [{ label: l.label, color: l.color, value: l.values[i] }];
        });

  return (
    <div className="ts-chart" ref={boxRef}>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={yLabel} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} className="ts-grid" />
            <text x={PAD.left - 6} y={y(v) + 4} className="ts-tick" textAnchor="end">
              {fmt(v)}
            </text>
          </g>
        ))}
        {xTicks.map((t, i) => (
          <text key={t} x={x(t)} y={height - 12} className="ts-tick" textAnchor={i === xTicks.length - 1 ? "end" : i === 0 ? "start" : "middle"}>
            {timeLabel(new Date(t).toISOString(), frequency)}
          </text>
        ))}
        <text x={12} y={PAD.top + (height - PAD.top - PAD.bottom) / 2} className="ts-tick" textAnchor="middle" transform={`rotate(-90 12 ${PAD.top + (height - PAD.top - PAD.bottom) / 2})`}>
          {yLabel}
        </text>
        {bands.map((b) => (
          <path
            key={b.key}
            d={`${path(b.ds, b.upper)}${[...b.ds].reverse().map((d, i) => `L${x(d).toFixed(1)},${y(b.lower[b.ds.length - 1 - i]).toFixed(1)}`).join("")}Z`}
            fill={b.color ?? CHART_COLORS.accent}
            opacity={b.opacity}
          />
        ))}
        {marker && (
          <g>
            <line x1={x(marker.ds)} x2={x(marker.ds)} y1={PAD.top} y2={height - PAD.bottom} className="ts-marker" />
            <text x={x(marker.ds) + 4} y={PAD.top + 10} className="ts-tick">
              {marker.label}
            </text>
          </g>
        )}
        {lines.map((l) => (
          <path key={l.key} d={path(l.ds, l.values)} fill="none" stroke={l.color} strokeWidth={l.width ?? 2} strokeDasharray={l.dashed ? "5 4" : undefined} />
        ))}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={height - PAD.bottom} className="ts-hover" />}
      </svg>
      <div className="ts-legend">
        {hover !== null && <span className="ts-legend-time">{timeLabel(new Date(hover).toISOString(), frequency)}</span>}
        {(hover !== null ? readout : lines.map((l) => ({ label: l.label, color: l.color, value: NaN }))).map((r) => (
          <span key={r.label} className="ts-legend-item">
            <i style={{ background: r.color }} />
            {r.label}
            {Number.isFinite(r.value) && <b>{fmt(r.value)}</b>}
          </span>
        ))}
      </div>
    </div>
  );
}

function seriesLines(s: ForecastSeries, levels: string[], forecastLabel: string): { lines: TimeLine[]; bands: TimeBand[] } {
  const lastHistory = { ds: s.history.ds.slice(-1), y: s.history.y.slice(-1) };
  return {
    lines: [
      { key: "history", label: "Observed", ds: s.history.ds, values: s.history.y, color: CHART_COLORS.dim, width: 1.5 },
      // Joined to the last observation so the forecast visibly continues the series.
      { key: "forecast", label: forecastLabel, ds: [...lastHistory.ds, ...s.forecast.ds], values: [...lastHistory.y, ...s.forecast.mean], color: CHART_COLORS.accent, width: 2.5 },
    ],
    bands: [...levels]
      .sort((a, b) => Number(b) - Number(a))
      .map((level, i) => ({
        key: level,
        ds: s.forecast.ds,
        lower: s.forecast.bands[level].lower,
        upper: s.forecast.bands[level].upper,
        opacity: i === 0 ? 0.14 : 0.24,
      })),
  };
}

function SeriesPicker({ forecast, value, onChange }: { forecast: Forecast; value: number; onChange: (i: number) => void }) {
  if (forecast.series.length < 2) return null;
  return (
    <label className="forecast-series-picker">
      Series{" "}
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {forecast.series.map((s, i) => (
          <option key={s.label} value={i}>
            {s.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function targetText(scenario: ScenarioSummary) {
  const units = scenario.target_units ? ` (${scenario.target_units})` : "";
  return { label: scenario.target_label ?? scenario.target ?? "Value", units, unit: scenario.target_units ?? "" };
}

// ── ML Predictions: the forecast ─────────────────────────────────────────────

export function ForecastPredictView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const { forecast, error } = useForecast(scenario, accessToken);
  const [index, setIndex] = useState(0);
  const target = targetText(scenario);
  const series = forecast?.series[index];

  useCopilotReadable({
    description: `The ${scenario.title} forecast on screen: the next ${forecast?.horizon ?? "?"} steps of ${target.label}, with prediction bands.`,
    value: series ? { series: series.label, forecast: series.forecast, peak: series.peak } : null,
  });

  if (error) return <div className="tab-panel"><div className="error">Could not load the forecast: {error}</div></div>;
  if (!forecast || !series) return <div className="tab-panel"><div className="panel-hint">Loading the forecast…</div></div>;
  const { lines, bands } = seriesLines(series, forecast.levels, "Forecast");
  const total = series.forecast.mean.reduce((a, b) => a + b, 0);
  const widest = forecast.levels.map(Number).sort((a, b) => b - a)[0];
  const exogenous = Object.keys(series.exogenous[0] ?? {});
  const schema = scenario.feature_schema ?? {};

  return (
    <div className="tab-panel forecast">
      <div className="kpi-row">
        <StatTile label="Peak" value={`${fmt(series.peak.value)} ${target.unit}`} sub={timeLabel(series.peak.ds, forecast.frequency)} highlight />
        <StatTile label={`Total, next ${forecast.horizon} steps`} value={`${fmt(total)} ${target.unit}`} />
        <StatTile label="Forecaster" value={candidateLabel(forecast.model_name)} sub="chosen by backtest (ML Insights)" />
      </div>
      <div className="panel-card">
        <div className="forecast-head">
          <h3>
            {target.label}: the next {forecast.horizon} steps
          </h3>
          <SeriesPicker forecast={forecast} value={index} onChange={setIndex} />
        </div>
        <TimeChart lines={lines} bands={bands} frequency={forecast.frequency} yLabel={`${target.label}${target.units}`} marker={{ ds: series.history.ds[series.history.ds.length - 1], label: "now" }} />
        <p className="panel-hint">
          Shaded: the {forecast.levels.join("% and ")}% prediction bands — calibrated on the backtest errors, so about {widest}% of real values should fall inside the wider one.
        </p>
      </div>
      <div className="panel-card">
        <h3>Step by step</h3>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Forecast{target.units}</th>
                {forecast.levels.map((l) => (
                  <th key={l}>{l}% band</th>
                ))}
                {exogenous.map((c) => (
                  <th key={c}>{schema[c]?.label ?? c} (assumed)</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {series.forecast.ds.map((d, i) => (
                <tr key={d} className={d === series.peak.ds ? "forecast-peak-row" : undefined}>
                  <td>{timeLabel(d, forecast.frequency)}</td>
                  <td>{fmt(series.forecast.mean[i])}</td>
                  {forecast.levels.map((l) => (
                    <td key={l}>
                      {fmt(series.forecast.bands[l].lower[i])} – {fmt(series.forecast.bands[l].upper[i])}
                    </td>
                  ))}
                  {exogenous.map((c) => (
                    <td key={c}>{series.exogenous[i][c].toFixed(1)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {exogenous.length > 0 && <p className="panel-hint">Future {exogenous.map((c) => schema[c]?.label ?? c).join(", ")} are assumptions (see the scenario), not observations.</p>}
      </div>
    </div>
  );
}

// ── ML Insights: the backtest ────────────────────────────────────────────────

export function ForecastInsightsView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [backtest, setBacktest] = useState<ForecastBacktest | null>(null);
  const [card, setCard] = useState<ModelCard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [windowIndex, setWindowIndex] = useState<number | null>(null);
  const target = targetText(scenario);

  useEffect(() => {
    let live = true;
    Promise.all([fetchForecastBacktest(config.predictionUrl, scenario.slug, accessToken), modelCard(config.predictionUrl, scenario.slug, accessToken)])
      .then(([b, c]) => {
        if (!live) return;
        setBacktest(b);
        setCard(c);
        setWindowIndex(b.windows.length - 1);
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [scenario.slug, accessToken]);

  useCopilotReadable({
    description: `The ${scenario.title} backtest on screen: each candidate forecaster's MASE/sMAPE/RMSE on past cut-offs and which one was selected.`,
    value: backtest ? { selected: backtest.selected, candidates: backtest.candidates } : null,
  });

  if (error) return <div className="tab-panel"><div className="error">Could not load the backtest: {error}</div></div>;
  if (!backtest || !card || windowIndex === null) return <div className="tab-panel"><div className="panel-hint">Loading the backtest…</div></div>;
  const selected = backtest.candidates.find((c) => c.name === backtest.selected);
  const baseline = backtest.candidates.find((c) => c.name === "seasonal_naive");
  const gain = selected && baseline && selected !== baseline ? 1 - selected.metrics.mase / baseline.metrics.mase : null;
  const schema = scenario.feature_schema ?? {};
  const window = backtest.windows[windowIndex];
  const s = window.series[0];
  const lines: TimeLine[] = [
    { key: "actual", label: "Actual", ds: s.ds, values: s.actual, color: CHART_COLORS.text, width: 2.5 },
    ...backtest.candidates.map((c, i) => ({
      key: c.name,
      label: candidateLabel(c.name),
      ds: s.ds,
      values: s.forecasts[c.name],
      color: CANDIDATE_COLORS[i % CANDIDATE_COLORS.length],
      dashed: c.name !== backtest.selected,
      width: c.name === backtest.selected ? 2.5 : 1.5,
    })),
  ];

  return (
    <div className="tab-panel forecast">
      <div className="kpi-row">
        <StatTile label="Selected" value={candidateLabel(backtest.selected)} sub={`of ${backtest.candidates.length} candidates`} highlight />
        <StatTile
          label="Backtest MASE"
          value={selected ? selected.metrics.mase.toFixed(3) : "—"}
          sub={gain !== null && gain > 0 ? `${(gain * 100).toFixed(0)}% less error than ${candidateLabel("seasonal_naive")}` : "lower is better"}
          info="Mean absolute scaled error: the forecast's average error on data it never saw, divided by the error of repeating the previous season on the training data. That scale is in-sample, so values above 1 are normal here: compare the candidates with each other."
        />
        <StatTile label="sMAPE" value={selected ? `${selected.metrics.smape.toFixed(1)}%` : "—"} info="Symmetric mean absolute percentage error, averaged over the backtest windows." />
        <StatTile label="RMSE" value={selected ? `${fmt(selected.metrics.rmse)} ${target.unit}` : "—"} />
      </div>
      <div className="grid-2">
        <div className="panel-card">
          <h3>Candidates by backtest error</h3>
          <BarList
            items={backtest.candidates.map((c) => ({ label: `${candidateLabel(c.name)}${c.name === backtest.selected ? " ✓" : ""}`, value: c.metrics.mase }))}
            signed={false}
            valueFormatter={(v) => `MASE ${v.toFixed(3)}`}
          />
          <p className="panel-hint">
            Green Code rule: a more complex forecaster is kept only when it lowers MASE by more than {card.accuracy_gain_threshold_for_complexity ?? 0} over the simpler one.
          </p>
        </div>
        <div className="panel-card">
          <h3>What drives the forecast</h3>
          {card.global_feature_importance.length ? (
            <>
              <BarList items={card.global_feature_importance.map((f) => ({ label: schema[f.feature]?.label ?? f.feature, value: f.importance }))} signed={false} valueFormatter={(v) => fmt(v)} />
              <p className="panel-hint">Mean |SHAP| of the LightGBM, grouped: recent history (lags and rolling statistics), calendar, and each other input.</p>
            </>
          ) : (
            <p className="panel-hint">The selected forecaster is statistical: it reads only the series' own past, so there is nothing else to attribute.</p>
          )}
        </div>
      </div>
      <div className="panel-card">
        <div className="forecast-head">
          <h3>Backtest: forecasts made at past cut-offs, against what happened</h3>
          <div className="sub-tabs">
            {backtest.windows.map((w, i) => (
              <button key={w.cutoff} className={i === windowIndex ? "active" : ""} onClick={() => setWindowIndex(i)}>
                {timeLabel(w.cutoff, "D")}
              </button>
            ))}
          </div>
        </div>
        <TimeChart lines={lines} frequency={scenario.time_series?.frequency ?? "h"} yLabel={`${target.label}${target.units}`} />
        <p className="panel-hint">
          Each candidate was trained only on data up to the cut-off and forecast the {backtest.horizon} steps after it — the same way the live forecast is made.
        </p>
      </div>
    </div>
  );
}

// ── forecast_board: what-if ──────────────────────────────────────────────────

export function ForecastBoardView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as ForecastBoardExtra;
  const { forecast, error } = useForecast(scenario, accessToken);
  const [index, setIndex] = useState(0);
  const [shifts, setShifts] = useState<Record<string, number>>({});
  const [whatIf, setWhatIf] = useState<ForecastSeries | null>(null);
  const [whatIfError, setWhatIfError] = useState<string | null>(null);
  const target = targetText(scenario);
  const base = forecast?.series[index];
  const shifted = Object.values(shifts).some((v) => v !== 0);

  useEffect(() => {
    if (!forecast || !base || !shifted || !forecast.what_if) return;
    let live = true;
    // Debounced: a slider drag fires many changes, one re-forecast is enough.
    const timer = window.setTimeout(() => {
      forecastWhatIf(config.predictionUrl, scenario.slug, base.label, shifts, accessToken)
        .then((r) => {
          if (!live) return;
          setWhatIf(r.series);
          setWhatIfError(null);
        })
        .catch((e: Error) => live && setWhatIfError(e.message));
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [forecast, base, shifted, shifts, scenario.slug, accessToken]);

  // Unshifted (or a model that ignores the levers): the baseline, never a stale what-if.
  const reForecast = shifted && forecast?.what_if && whatIf?.label === base?.label ? whatIf : null;
  const shown = reForecast ?? base;
  useCopilotReadable({
    description: `The ${scenario.title} forecast board: the baseline forecast, the what-if shifts the user set on the levers, and the re-forecast.`,
    value: base && shown ? { shifts, baseline_peak: base.peak, what_if_peak: shown.peak } : null,
  });

  if (error) return <div className="tab-panel"><div className="error">Could not load the forecast: {error}</div></div>;
  if (!forecast || !base || !shown) return <div className="tab-panel"><div className="panel-hint">Loading the forecast…</div></div>;
  const total = (s: ForecastSeries) => s.forecast.mean.reduce((a, b) => a + b, 0);
  const delta = total(shown) - total(base);
  const { lines, bands } = seriesLines(shown, forecast.levels, reForecast ? "What-if forecast" : "Forecast");
  if (reForecast) lines.push({ key: "baseline", label: "Baseline forecast", ds: base.forecast.ds, values: base.forecast.mean, color: CHART_COLORS.amber, dashed: true, width: 1.5 });

  return (
    <div className="tab-panel forecast forecast-board">
      <div className="forecast-board-title">
        <h2>{extras.title}</h2>
        {extras.subtitle && <p className="panel-hint">{extras.subtitle}</p>}
      </div>
      <div className="kpi-row">
        <StatTile
          label={extras.peak_label}
          value={timeLabel(shown.peak.ds, forecast.frequency)}
          sub={`${fmt(shown.peak.value)} ${target.unit}${reForecast && shown.peak.ds !== base.peak.ds ? ` (was ${timeLabel(base.peak.ds, forecast.frequency)})` : ""}`}
          highlight
        />
        <StatTile label={`Total, next ${forecast.horizon} steps`} value={`${fmt(total(shown))} ${target.unit}`} sub={reForecast ? `${delta >= 0 ? "+" : ""}${fmt(delta)} (${((delta / total(base)) * 100).toFixed(1)}%) vs baseline` : "baseline"} color={reForecast ?(delta > 0 ? CHART_COLORS.red : CHART_COLORS.green) : undefined} />
      </div>
      <div className="forecast-board-body">
        <div className="panel-card forecast-levers">
          <h3>What if…</h3>
          <SeriesPicker forecast={forecast} value={index} 
            onChange={(i) => {
              setIndex(i);
              setShifts({});
            }}
          />
          {!forecast.what_if && <p className="panel-hint">The selected forecaster ({candidateLabel(forecast.model_name)}) reads only the series' own past, so these inputs cannot move it.</p>}
          {extras.levers.map((lever) => {
            const value = shifts[lever.feature] ?? 0;
            return (
              <label key={lever.feature} className="forecast-lever">
                <span>
                  {lever.label} <b>{value > 0 ? "+" : ""}{value} {lever.unit}</b>
                </span>
                <input
                  type="range"
                  min={lever.min_shift}
                  max={lever.max_shift}
                  step={lever.step}
                  value={value}
                  disabled={!forecast.what_if}
                  onChange={(e) => setShifts((s) => ({ ...s, [lever.feature]: Number(e.target.value) }))}
                />
              </label>
            );
          })}
          {shifted && (
            <button className="btn-secondary" onClick={() => setShifts({})}>
              Reset
            </button>
          )}
          {whatIfError && <div className="error">{whatIfError}</div>}
          <p className="panel-hint">Each lever shifts the assumed value over the whole forecast window; the model re-forecasts step by step from there.</p>
        </div>
        <div className="panel-card">
          <TimeChart lines={lines} bands={bands} frequency={forecast.frequency} yLabel={`${target.label}${target.units}`} marker={{ ds: shown.history.ds[shown.history.ds.length - 1], label: "now" }} height={320} />
        </div>
      </div>
    </div>
  );
}
