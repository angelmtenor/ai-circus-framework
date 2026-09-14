import { useCallback, useEffect, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { predict, type ScenarioSummary, type LivePlantExtra, type FeatureSpec } from "./apiClient";
import { config } from "./config";
import { Gauge, CHART_COLORS } from "./charts";
import { Icon } from "./Icon";
import { initialRecord, featureLabel, type Record_ } from "./predictUtils";

type MachineStatus = "running" | "stopped";

type Machine = {
  id: string;
  name: string;
  status: MachineStatus;
  record: Record_;
  stress: number; // 0..1 — ramps during a simulated at-risk episode, then self-recovers
  probability: number | null; // last /predict result, null while stopped or before the first tick
  history: number[]; // last few probabilities, for the tile's trend bar
};

const STRESS_START_CHANCE = 0.03;
const STRESS_STEP = 0.16;
const WALK_SIGMA_FRACTION = 0.02; // fraction of a numeric feature's (max-min) span per tick
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
  if (status === "stopped") return CHART_COLORS.dim;
  if (probability === null) return CHART_COLORS.dim;
  if (probability >= 0.1) return CHART_COLORS.red;
  if (probability >= 0.01) return CHART_COLORS.amber;
  return CHART_COLORS.green;
}

function statusLabel(status: MachineStatus, probability: number | null): string {
  if (status === "stopped") return "Stopped";
  if (probability === null) return "Starting…";
  if (probability >= 0.1) return "Critical";
  if (probability >= 0.01) return "Warning";
  return "Healthy";
}

function makeMachine(id: string, name: string, featureColumns: string[], featureSchema: Record<string, FeatureSpec>): Machine {
  return {
    id,
    name,
    status: "running",
    record: initialRecord(featureColumns, featureSchema),
    stress: 0,
    probability: null,
    history: [],
  };
}

/** One tick's worth of client-side sensor simulation for a running machine — a
 * bounded random walk on every numeric feature, biased toward the top of each
 * feature's range while `stress` is elevated (a simulated at-risk episode), and
 * relaxing back toward the walk once `stress` decays to 0. Fully generic — reads
 * feature bounds from the scenario's own feature_schema, no scenario-specific
 * column names — so this same simulator would drive any tabular_ml scenario's
 * live_plant tab, not just mpm.
 */
function stepMachine(m: Machine, numeric: string[], featureSchema: Record<string, FeatureSpec>): Machine {
  if (m.status === "stopped") return m;

  let stress = m.stress;
  if (stress === 0 && Math.random() < STRESS_START_CHANCE) {
    stress = STRESS_STEP;
  } else if (stress > 0) {
    stress = stress >= 1 ? 0 : clamp(stress + STRESS_STEP, 0, 1);
  }

  const record: Record_ = { ...m.record };
  for (const f of numeric) {
    const spec = featureSchema[f];
    if (spec.type !== "numeric") continue;
    const span = spec.max - spec.min;
    const current = Number(record[f]);
    const walk = gaussian() * span * WALK_SIGMA_FRACTION;
    const stressPull = stress > 0 ? stress * (spec.max - current) * 0.35 : 0;
    record[f] = Number(clamp(current + walk + stressPull, spec.min, spec.max).toFixed(3));
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
 * real actuation behind "Shut down"/"Restart"; both are called out in the caption
 * below so the demo control is never mistaken for a real one.
 */
export function LivePlantView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as LivePlantExtra;
  const featureColumns = scenario.feature_columns ?? [];
  const featureSchema = scenario.feature_schema ?? {};
  const numeric = numericFeatures(featureColumns, featureSchema);

  const [machines, setMachines] = useState<Machine[]>(() =>
    Array.from({ length: extras.machine_count }, (_, i) => makeMachine(`m${i + 1}`, `${extras.machine_label_prefix} #${i + 1}`, featureColumns, featureSchema)),
  );
  const [running, setRunning] = useState(true);
  const [tickCount, setTickCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const machinesRef = useRef(machines);
  machinesRef.current = machines;

  const tick = useCallback(async () => {
    const stepped = machinesRef.current.map((m) => stepMachine(m, numeric, featureSchema));
    const toScore = stepped.filter((m) => m.status === "running");
    if (toScore.length === 0) {
      setMachines(stepped);
      setTickCount((c) => c + 1);
      return;
    }
    try {
      const records = toScore.map((m) => m.record);
      const response = await predict(config.predictionUrl, scenario.slug, records, accessToken);
      let ri = 0;
      const next = stepped.map((m) => {
        if (m.status !== "running") return m;
        const p = response.predictions[ri];
        ri += 1;
        const history = [...m.history, p.prediction].slice(-HISTORY_LEN);
        return { ...m, probability: p.prediction, history };
      });
      setMachines(next);
      setError(null);
    } catch (e) {
      setMachines(stepped);
      setError((e as Error).message);
    } finally {
      setTickCount((c) => c + 1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenario.slug, accessToken]);

  useEffect(() => {
    if (!running) return;
    void tick();
    const id = setInterval(() => void tick(), extras.tick_seconds * 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, extras.tick_seconds, tick]);

  function shutDown(id: string) {
    setMachines((prev) => prev.map((m) => (m.id === id ? { ...m, status: "stopped", probability: null, stress: 0 } : m)));
  }

  function restart(id: string) {
    setMachines((prev) => prev.map((m) => (m.id === id ? makeMachine(m.id, m.name, featureColumns, featureSchema) : m)));
  }

  useCopilotReadable({
    description: `The live plant floor for ${scenario.title}, currently on screen: each fictional machine's status and last predicted failure probability, updated every ${extras.tick_seconds}s. Use this if asked which machine is at risk or currently shut down.`,
    value: machines.map((m) => ({ name: m.name, status: m.status, failure_probability: m.probability })),
  });

  const atRiskCount = machines.filter((m) => m.status === "running" && (m.probability ?? 0) >= 0.01).length;

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <div className="plant-floor-toolbar">
          <div>
            <h3>Live plant floor</h3>
            <p className="panel-hint">
              {extras.machine_count} fictional machines, simulated client-side and scored every {extras.tick_seconds}s by this scenario's real, unmodified{" "}
              <code>/predict/{scenario.slug}</code>. Sensor readings and the Shut down/Restart controls are a demo simulation only — nothing here reads real
              telemetry or actuates real hardware.
            </p>
          </div>
          <div className="plant-floor-controls">
            <span className="panel-hint">Tick #{tickCount}</span>
            {atRiskCount > 0 && <span className="badge badge--warning">{atRiskCount} at risk</span>}
            <button className="btn-secondary" onClick={() => setRunning((r) => !r)}>
              {running ? "⏸ Pause floor" : "▶ Resume floor"}
            </button>
          </div>
        </div>
        {error && <p className="error">{error}</p>}
      </div>

      <div className="plant-floor">
        <div className="plant-floor-belt" aria-hidden="true" />
        {machines.map((m) => (
          <div key={m.id} className={`machine-tile machine-tile--${m.status === "stopped" ? "stopped" : statusLabel(m.status, m.probability).toLowerCase()}`}>
            <div className="machine-tile-header">
              <Icon name="factory" size={18} />
              <span>{m.name}</span>
            </div>
            <div className="machine-tile-status" style={{ color: statusColor(m.status, m.probability) }}>
              {statusLabel(m.status, m.probability)}
            </div>
            <Gauge value={m.probability ?? 0} size={92} color={statusColor(m.status, m.probability)} label="failure risk" />
            <div className="machine-tile-readouts">
              {featureColumns.slice(0, 3).map((f) => (
                <div key={f} className="machine-tile-readout">
                  <span>{featureLabel(scenario, f)}</span>
                  <strong>{typeof m.record[f] === "number" ? (m.record[f] as number).toFixed(1) : m.record[f]}</strong>
                </div>
              ))}
            </div>
            {m.status === "running" ? (
              <button className="btn-danger" onClick={() => shutDown(m.id)}>
                Shut down
              </button>
            ) : (
              <button className="btn-secondary" onClick={() => restart(m.id)}>
                Restart
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
