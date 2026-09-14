import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { predict, type ScenarioSummary, type LivePlantExtra, type FeatureSpec } from "./apiClient";
import { config } from "./config";
import { Gauge, BarList, CHART_COLORS } from "./charts";
import { Icon } from "./Icon";
import { initialRecord, featureLabel, mapContributions, type Record_ } from "./predictUtils";

type MachineStatus = "running" | "stopped" | "broken";

type Machine = {
  id: string;
  name: string;
  status: MachineStatus;
  record: Record_;
  stress: number; // 0..1 — only meaningful as the "wear trend" proxy when the
  // scenario has no wear_feature (see stepMachine); ignored otherwise.
  probability: number | null; // last /predict result, null before the first tick
  contributions: Record<string, number>; // last SHAP explanation, for the click-to-drill-down panel
  history: number[]; // last few probabilities, unused visually today but kept for a future trend sparkline
};

const STRESS_START_CHANCE = 0.03;
const STRESS_STEP = 0.16;
const BASE_SIGMA_FRACTION = 0.015; // baseline per-tick noise, as a fraction of a feature's (max-min) span
const NOISE_AMPLIFY = 2.5; // how much noisier a feature gets as wear trend -> 1
const DRIFT_FRACTION_PER_TICK = 0.012; // per-tick upward pull, as a fraction of span, at full wear trend
const HISTORY_LEN = 12;

function numericFeatures(featureColumns: string[], featureSchema: Record<string, FeatureSpec>): string[] {
  return featureColumns.filter((f) => featureSchema[f]?.type === "numeric");
}

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

function gaussian(): number {
  // Box-Muller — good enough for a cosmetic simulation, no need for a real RNG library.
  const u = Math.max(Math.random(), 1e-9);
  const v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function statusColor(status: MachineStatus, probability: number | null): string {
  if (status === "broken") return CHART_COLORS.red;
  if (status === "stopped") return CHART_COLORS.dim;
  if (probability === null) return CHART_COLORS.dim;
  if (probability >= 0.1) return CHART_COLORS.red;
  if (probability >= 0.01) return CHART_COLORS.amber;
  return CHART_COLORS.green;
}

function statusLabel(status: MachineStatus, probability: number | null): string {
  if (status === "broken") return "Broken";
  if (status === "stopped") return "Stopped";
  if (probability === null) return "Starting…";
  if (probability >= 0.1) return "Critical";
  if (probability >= 0.01) return "Warning";
  return "Healthy";
}

function freshRecord(featureColumns: string[], featureSchema: Record<string, FeatureSpec>, wearFeature: string | null): Record_ {
  const record = initialRecord(featureColumns, featureSchema);
  // A serviced/replaced machine's wear resets to its real minimum (a fresh tool),
  // not the dataset's statistical default (a typical mid-life reading) — every
  // other feature still starts from its normal default operating point.
  if (wearFeature) {
    const spec = featureSchema[wearFeature];
    if (spec?.type === "numeric") record[wearFeature] = spec.min;
  }
  return record;
}

function makeMachine(id: string, name: string, featureColumns: string[], featureSchema: Record<string, FeatureSpec>, wearFeature: string | null): Machine {
  return {
    id,
    name,
    status: "running",
    record: freshRecord(featureColumns, featureSchema, wearFeature),
    stress: 0,
    probability: null,
    contributions: {},
    history: [],
  };
}

function formatClock(d: Date): string {
  return d.toLocaleString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** One tick's worth of client-side sensor simulation for a running machine.
 *
 * If the scenario names a `wearFeature` (e.g. mpm's "Tool wear [min]"), that
 * feature accumulates deterministically by `simMinutesPerTick` each tick (clamped
 * to its own real feature_schema max) — a genuine, trained consequence of running
 * longer, not a scripted animation. Every OTHER numeric feature gets a bounded
 * random walk whose noise and *upward* drift both scale with how worn the machine
 * is ("wear trend", 0..1) — physically: a worn machine runs noisier and strains
 * harder, which is what actually pushes the real model's predicted risk up over a
 * session, not a hardcoded "torque always rises" rule.
 *
 * With no `wearFeature` at all, "wear trend" falls back to `stress`, an episodic
 * 0..1 ramp-then-reset — so this same simulator still produces a reasonable arc
 * for a future live_plant scenario with no obvious wear-type column.
 */
function stepMachine(m: Machine, numeric: string[], featureSchema: Record<string, FeatureSpec>, wearFeature: string | null, simMinutesPerTick: number): Machine {
  if (m.status !== "running") return m;

  const record: Record_ = { ...m.record };
  let wearTrend: number;
  let stress = m.stress;

  if (wearFeature) {
    const spec = featureSchema[wearFeature];
    if (spec?.type === "numeric") {
      const next = clamp(Number(record[wearFeature]) + simMinutesPerTick, spec.min, spec.max);
      record[wearFeature] = Number(next.toFixed(2));
      wearTrend = (next - spec.min) / (spec.max - spec.min || 1);
    } else {
      wearTrend = 0;
    }
  } else {
    if (stress === 0 && Math.random() < STRESS_START_CHANCE) stress = STRESS_STEP;
    else if (stress > 0) stress = stress >= 1 ? 0 : clamp(stress + STRESS_STEP, 0, 1);
    wearTrend = stress;
  }

  for (const f of numeric) {
    if (f === wearFeature) continue;
    const spec = featureSchema[f];
    if (spec.type !== "numeric") continue;
    const span = spec.max - spec.min;
    const current = Number(record[f]);
    const sigma = span * BASE_SIGMA_FRACTION * (1 + NOISE_AMPLIFY * wearTrend);
    const drift = span * DRIFT_FRACTION_PER_TICK * wearTrend;
    record[f] = Number(clamp(current + gaussian() * sigma + drift, spec.min, spec.max).toFixed(3));
  }

  return { ...m, record, stress };
}

/**
 * Generic 5th workspace tab for any `tabular_ml` scenario that sets
 * `ui_extras: {kind: live_plant, ...}` (see libs/shared/scenario_schema.py) — mpm
 * is the first user, but this renderer has no mpm-specific code: it drives its
 * fictional machines entirely off the scenario's own feature_columns/feature_schema
 * and scores them with that same scenario's existing, unmodified /predict/{slug}.
 *
 * A `setInterval` client-side simulation only — no real telemetry ingestion and no
 * real actuation behind Shut down/Restart/Maintenance/Replace; all are called out
 * in the caption below so the demo controls are never mistaken for real ones. Each
 * tick's own predicted failure probability is used directly as that tick's failure
 * *hazard* (a stochastic roll, not a fixed threshold) — a running machine can break
 * on its own once real risk is non-trivial, then needs Replace before it runs again.
 */
export function LivePlantView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as LivePlantExtra;
  const featureColumns = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const featureSchema = useMemo(() => scenario.feature_schema ?? {}, [scenario.feature_schema]);
  const numeric = useMemo(() => numericFeatures(featureColumns, featureSchema), [featureColumns, featureSchema]);
  // Every numeric reading is worth showing (wear/torque-style features are exactly
  // what makes the simulation's story legible) — categorical ones (e.g. machine
  // type) rarely change tick to tick and are skipped from the compact readout list.
  const readoutColumns = numeric;

  const [machines, setMachines] = useState<Machine[]>(() =>
    Array.from({ length: extras.machine_count }, (_, i) => makeMachine(`m${i + 1}`, `${extras.machine_label_prefix} #${i + 1}`, featureColumns, featureSchema, extras.wear_feature)),
  );
  const [running, setRunning] = useState(true);
  const [simClock, setSimClock] = useState(() => new Date());
  const [detailId, setDetailId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const machinesRef = useRef(machines);
  machinesRef.current = machines;

  const tick = useCallback(async () => {
    const stepped = machinesRef.current.map((m) => stepMachine(m, numeric, featureSchema, extras.wear_feature, extras.sim_minutes_per_tick));
    // Applied immediately (not held back for the predict round-trip below) — sensor
    // readouts stay live even under slow network/backend latency, and it closes a
    // race window: without this, a Shut down/Restart/Maintenance/Replace click that
    // lands *while* this tick's request is in flight would otherwise get silently
    // reverted when that request resolves and overwrites the whole machine list from
    // a snapshot taken before the click.
    setMachines(stepped);
    const toScore = stepped.filter((m) => m.status === "running");
    setSimClock((c) => new Date(c.getTime() + extras.sim_minutes_per_tick * 60_000));
    if (toScore.length === 0) return;
    try {
      const records = toScore.map((m) => m.record);
      const response = await predict(config.predictionUrl, scenario.slug, records, accessToken);
      const resultById = new Map(toScore.map((m, i) => [m.id, response.predictions[i]]));
      setMachines((latest) =>
        latest.map((m) => {
          const result = resultById.get(m.id);
          // Only merge this tick's prediction into a machine that's STILL running
          // — the user may have shut it down (or Replace/Maintenance may have
          // reset it) while this request was in flight, in which case their own,
          // newer action wins instead of being clobbered by a now-stale result.
          if (!result || m.status !== "running") return m;
          // The model's own predicted risk IS this tick's failure hazard — no
          // separate arbitrary threshold, so a break is a genuine (if stochastic)
          // consequence of what the trained model actually says right now.
          const broke = Math.random() < result.prediction;
          const status: MachineStatus = broke ? "broken" : "running";
          const history = [...m.history, result.prediction].slice(-HISTORY_LEN);
          return { ...m, probability: result.prediction, contributions: result.contributions, history, status };
        }),
      );
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenario.slug, accessToken, numeric, extras.wear_feature, extras.sim_minutes_per_tick]);

  useEffect(() => {
    if (!running) return;
    void tick();
    const id = setInterval(() => void tick(), extras.tick_seconds * 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, extras.tick_seconds, tick]);

  function shutDown(id: string) {
    setMachines((prev) => prev.map((m) => (m.id === id ? { ...m, status: "stopped" } : m)));
  }

  function restart(id: string) {
    // Resumes as-is — wear/sensor state carries over, same as flipping real
    // equipment back on without servicing it (see Maintenance for the reset).
    setMachines((prev) => prev.map((m) => (m.id === id ? { ...m, status: "running" } : m)));
  }

  // Maintenance (from stopped) and Replace (from broken) both fully reset a
  // machine — wear back to its real minimum, every other sensor back to its
  // normal default, back into service — the only difference is which state each
  // button is offered from.
  function resetToFresh(id: string) {
    setMachines((prev) => prev.map((m) => (m.id === id ? makeMachine(m.id, m.name, featureColumns, featureSchema, extras.wear_feature) : m)));
  }

  useCopilotReadable({
    description: `The live plant floor for ${scenario.title}, currently on screen at simulated time ${formatClock(simClock)}: each fictional machine's status and last predicted failure probability. Use this if asked which machine is at risk, stopped, or broken.`,
    value: machines.map((m) => ({ name: m.name, status: m.status, failure_probability: m.probability })),
  });

  const atRiskCount = machines.filter((m) => m.status === "running" && (m.probability ?? 0) >= 0.01).length;
  const brokenCount = machines.filter((m) => m.status === "broken").length;

  const detailCandidate = detailId ? (machines.find((m) => m.id === detailId) ?? null) : null;
  // A machine just Replaced/serviced still resolves here (same id) but has no
  // contributions yet — treat that the same as nothing selected rather than
  // rendering a "failure risk: 0.00%" with an empty bar list underneath it.
  const detailMachine = detailCandidate && Object.keys(detailCandidate.contributions).length > 0 ? detailCandidate : null;
  const detailContributions = detailMachine
    ? mapContributions(detailMachine.record, detailMachine.contributions).map((item) => ({ ...item, label: featureLabel(scenario, item.label) }))
    : [];

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <div className="plant-floor-toolbar">
          <div>
            <h3>Live plant floor</h3>
            <p className="panel-hint">
              {extras.machine_count} fictional machines, simulated client-side and scored every {extras.tick_seconds}s by this scenario's real, unmodified{" "}
              <code>/predict/{scenario.slug}</code> — each tick advances the clock by {extras.sim_minutes_per_tick} simulated minutes, ages the machines, and can
              break one on its own (a stochastic roll using the model's own predicted risk). Sensor readings and every control below are a demo simulation only —
              nothing here reads real telemetry or actuates real hardware.
            </p>
          </div>
          <div className="plant-floor-controls">
            <span className="panel-hint plant-floor-clock">🕒 {formatClock(simClock)}</span>
            {atRiskCount > 0 && <span className="badge badge--warning">{atRiskCount} at risk</span>}
            {brokenCount > 0 && <span className="badge badge--danger">{brokenCount} broken</span>}
            <button className="btn-secondary" onClick={() => setRunning((r) => !r)}>
              {running ? "⏸ Pause floor" : "▶ Resume floor"}
            </button>
          </div>
        </div>
        {error && <p className="error">{error}</p>}
      </div>

      <div className="plant-floor">
        <div className="plant-floor-belt" aria-hidden="true" />
        {machines.map((m) => {
          const runningModifier = m.probability === null ? "" : statusLabel(m.status, m.probability).toLowerCase();
          const tileModifier = m.status === "running" ? runningModifier : m.status;
          return (
            <div key={m.id} className={`machine-tile${tileModifier ? ` machine-tile--${tileModifier}` : ""}`}>
              <div className="machine-tile-header">
                <Icon name="factory" size={18} />
                <span>{m.name}</span>
              </div>
              <div className="machine-tile-status" style={{ color: statusColor(m.status, m.probability) }}>
                {statusLabel(m.status, m.probability)}
              </div>
              <button
                className="machine-tile-gauge"
                onClick={() => setDetailId(m.id)}
                disabled={Object.keys(m.contributions).length === 0}
                title="Click to see this prediction's SHAP explanation"
              >
                <Gauge value={m.probability ?? 0} size={92} color={statusColor(m.status, m.probability)} label="failure risk · click" />
              </button>
              <div className="machine-tile-readouts">
                {readoutColumns.map((f) => (
                  <div key={f} className={`machine-tile-readout${f === extras.wear_feature ? " machine-tile-readout--wear" : ""}`}>
                    <span>{featureLabel(scenario, f)}</span>
                    <strong>{typeof m.record[f] === "number" ? (m.record[f] as number).toFixed(1) : m.record[f]}</strong>
                  </div>
                ))}
              </div>
              {m.status === "running" && (
                <button className="btn-danger" onClick={() => shutDown(m.id)}>
                  Shut down
                </button>
              )}
              {m.status === "stopped" && (
                <div className="machine-tile-button-row">
                  <button className="btn-secondary" onClick={() => restart(m.id)}>
                    Restart
                  </button>
                  <button className="btn-secondary" onClick={() => resetToFresh(m.id)}>
                    Maintenance
                  </button>
                </div>
              )}
              {m.status === "broken" && (
                <button className="btn-primary" onClick={() => resetToFresh(m.id)}>
                  Replace
                </button>
              )}
            </div>
          );
        })}
      </div>

      <div className="panel-card">
        <h3>SHAP explanation</h3>
        {detailMachine ? (
          <>
            <p className="panel-hint">
              {detailMachine.name} — failure risk: <strong>{((detailMachine.probability ?? 0) * 100).toFixed(2)}%</strong>
            </p>
            <BarList items={detailContributions} valueFormatter={(v) => v.toFixed(4)} />
          </>
        ) : detailCandidate ? (
          <p className="panel-hint">{detailCandidate.name} was just reset — waiting for its next prediction.</p>
        ) : (
          <p className="panel-hint">Click a machine's gauge above to see what's driving its current prediction.</p>
        )}
      </div>
    </div>
  );
}
