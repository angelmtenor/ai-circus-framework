/**
 * Generic 5th workspace tab (`ui_extras: {kind: fleet_health}`, see
 * scenario_schema.FleetHealthExtra): a fleet of units replaying real rows of the dataset, each
 * unit's current row scored by the deployed model at every tick. Health is the model's score, or
 * how far the real value strays from it when the target is not itself a health measure. Nothing is
 * simulated, unlike LivePlantView. DOM and CSS transitions only: no canvas and no frame loop, and
 * ticks stop while the page is hidden. Status never relies on colour alone (glyph + label).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { datasetSample, predict, type FleetHealthExtra, type FleetHealthMeasure, type ScenarioSummary } from "./apiClient";
import { BarList, CHART_COLORS, StatTile } from "./charts";
import { config } from "./config";
import { explainByFeature, featureLabel, type Record_ } from "./predictUtils";
import "./fleetHealth.css";

type Row = Record<string, unknown>;
type Status = "healthy" | "watch" | "critical";
type Reading = { predicted: number; actual: number; health: number };
type Unit = { id: string; label: string; rows: Row[]; pos: number; rowId: string; history: Reading[] };
type Explanation = { unitId: string; rowId: string; items: { label: string; value: number }[] };

const HISTORY = 40;
const STATUS: Record<Status, { glyph: string; label: string; color: string }> = {
  healthy: { glyph: "●", label: "Healthy", color: CHART_COLORS.green },
  watch: { glyph: "▲", label: "Watch", color: CHART_COLORS.amber },
  critical: { glyph: "◆", label: "Critical", color: CHART_COLORS.red },
};

function statusOf(health: number, m: FleetHealthMeasure): Status {
  if (m.direction === "lower_is_worse") return health <= m.critical_at ? "critical" : health <= m.warn_at ? "watch" : "healthy";
  const value = m.direction === "absolute" ? Math.abs(health) : health;
  return value >= m.critical_at ? "critical" : value >= m.warn_at ? "watch" : "healthy";
}

/** 0 well inside the healthy range, 1 at the critical threshold. */
function severity(health: number, m: FleetHealthMeasure): number {
  const value = m.direction === "absolute" ? Math.abs(health) : health;
  const safe = m.warn_at + (m.warn_at - m.critical_at);
  const start = m.direction === "lower_is_worse" ? safe : Math.max(0, safe);
  return Math.min(1, Math.max(0, (value - start) / (m.critical_at - start)));
}

/** The units and their rows, in replay order, each starting at a different point of its rows. */
function buildUnits(rows: Row[], idColumn: string | null, extras: FleetHealthExtra): Unit[] {
  const noun = extras.unit_noun[0].toUpperCase() + extras.unit_noun.slice(1);
  let groups: [string, Row[]][];
  if (extras.group_column) {
    const byValue = new Map<string, Row[]>();
    for (const row of rows) {
      const key = String(row[extras.group_column]);
      const members = byValue.get(key);
      if (members) members.push(row);
      else byValue.set(key, [row]);
    }
    groups = [...byValue.entries()].sort((a, b) => b[1].length - a[1].length).slice(0, extras.unit_count);
    if (extras.order_column) {
      const column = extras.order_column;
      for (const [, members] of groups) members.sort((a, b) => Number(a[column]) - Number(b[column]));
    }
    groups.sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }));
  } else {
    groups = Array.from({ length: extras.unit_count }, (_, i): [string, Row[]] => [String(i + 1), []]);
    rows.forEach((row, i) => groups[i % extras.unit_count][1].push(row));
  }
  return groups.map(([key, members], i) => {
    const pos = Math.floor((members.length * i) / groups.length);
    return {
      id: key,
      label: `${noun} ${key[0].toUpperCase()}${key.slice(1).replaceAll("_", " ")}`,
      rows: members,
      pos,
      rowId: idColumn ? String(members[pos]?.[idColumn] ?? "") : "",
      history: [],
    };
  });
}

function Sparkline({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return <div className="fleet-spark" />;
  const lo = Math.min(...values);
  const span = Math.max(...values) - lo || 1;
  const points = values.map((v, i) => `${(i / (HISTORY - 1)) * 100},${22 - ((v - lo) / span) * 20}`).join(" ");
  return (
    <svg className="fleet-spark" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={points} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function ReplayChart({ history, label, format }: { history: Reading[]; label: string; format: (v: number) => string }) {
  if (history.length < 2) return <p className="panel-hint">Collecting readings…</p>;
  const values = history.flatMap((r) => [r.predicted, r.actual]);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const W = 640;
  const H = 170;
  const x = (i: number) => 52 + (i / (history.length - 1)) * (W - 62);
  const y = (v: number) => 10 + (1 - (v - lo) / span) * (H - 22);
  const line = (key: "predicted" | "actual") => history.map((r, i) => `${x(i).toFixed(1)},${y(r[key]).toFixed(1)}`).join(" ");
  return (
    <div className="fleet-replay">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}: actual and predicted over the last ${history.length} readings`}>
        {[hi, lo].map((v) => (
          <g key={v}>
            <line x1={52} x2={W - 10} y1={y(v)} y2={y(v)} className="fleet-grid" />
            <text x={46} y={y(v) + 4} textAnchor="end" className="fleet-tick">
              {format(v)}
            </text>
          </g>
        ))}
        <polyline points={line("actual")} fill="none" stroke={CHART_COLORS.text} strokeWidth="2" />
        <polyline points={line("predicted")} fill="none" stroke={CHART_COLORS.accent} strokeWidth="2" strokeDasharray="5 4" />
        {history.map((r, i) => (
          <circle key={i} cx={x(i)} cy={y(r.actual)} r="6" fill="transparent">
            <title>{`Actual ${format(r.actual)} · predicted ${format(r.predicted)}`}</title>
          </circle>
        ))}
      </svg>
      <div className="fleet-legend">
        <span>
          <i style={{ background: CHART_COLORS.text }} /> Actual
        </span>
        <span>
          <i style={{ background: CHART_COLORS.accent }} /> Predicted
        </span>
      </div>
    </div>
  );
}

export function FleetHealthView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as FleetHealthExtra;
  const health = extras.health;
  const features = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const percent = scenario.task_type === "classification";
  const format = useCallback(
    (v: number) => (percent ? `${(v * 100).toFixed(0)}%` : Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)),
    [percent],
  );
  const healthText = (v: number) => `${format(v)}${!percent && health.unit ? ` ${health.unit}` : ""}`;

  const [units, setUnits] = useState<Unit[]>([]);
  const [idColumn, setIdColumn] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const unitsRef = useRef(units);
  useEffect(() => {
    unitsRef.current = units;
  }, [units]);
  const scoring = useRef(false);

  useEffect(() => {
    let live = true;
    datasetSample(config.predictionUrl, scenario.slug, 30000, accessToken)
      .then((sample) => {
        if (!live) return;
        setIdColumn(sample.id_column ?? null);
        setUnits(buildUnits(sample.rows as Row[], sample.id_column ?? null, extras));
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [scenario.slug, accessToken, extras]);

  const tick = useCallback(async () => {
    const current = unitsRef.current.filter((u) => u.rows.length > 0);
    if (scoring.current || document.hidden || current.length === 0) return;
    scoring.current = true;
    try {
      const records = current.map((u) => Object.fromEntries(features.map((f) => [f, u.rows[u.pos][f]])) as Record_);
      const response = await predict(config.predictionUrl, scenario.slug, records, accessToken, { explain: false });
      const target = scenario.target ?? "";
      const scored = new Map(
        current.map((u, i): [string, Reading] => {
          const predicted = response.predictions[i].prediction;
          const actual = Number(u.rows[u.pos][target]);
          return [u.id, { predicted, actual, health: health.value === "residual" ? actual - predicted : predicted }];
        }),
      );
      setUnits((latest) =>
        latest.map((u) => {
          const reading = scored.get(u.id);
          if (!reading) return u;
          const wraps = u.pos + extras.rows_per_tick >= u.rows.length;
          const pos = wraps ? 0 : u.pos + extras.rows_per_tick;
          return {
            ...u,
            pos,
            rowId: idColumn ? String(u.rows[u.pos][idColumn] ?? "") : "",
            // A unit's replay ending starts a new life: its old history would draw a false jump.
            history: wraps ? [] : [...u.history, reading].slice(-HISTORY),
          };
        }),
      );
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      scoring.current = false;
    }
  }, [features, scenario.slug, scenario.target, accessToken, health.value, extras.rows_per_tick, idColumn]);

  const loaded = units.length > 0;
  useEffect(() => {
    if (!running || !loaded) return;
    void tick();
    const id = window.setInterval(() => void tick(), extras.tick_seconds * 1000);
    return () => window.clearInterval(id);
  }, [running, loaded, tick, extras.tick_seconds]);

  const latest = (u: Unit) => u.history[u.history.length - 1] as Reading | undefined;
  const statuses = units.map((u) => {
    const reading = latest(u);
    return reading ? statusOf(reading.health, health) : null;
  });
  const count = (s: Status) => statuses.filter((v) => v === s).length;
  const selected = units.find((u) => u.id === selectedId) ?? null;
  const selectedReading = selected ? latest(selected) : undefined;

  function select(unit: Unit) {
    setSelectedId(unit.id);
    setExplanation(null);
    const row = unit.rows[Math.max(0, unit.pos - extras.rows_per_tick)];
    const record = Object.fromEntries(features.map((f) => [f, row[f]])) as Record_;
    predict(config.predictionUrl, scenario.slug, [record], accessToken)
      .then((response) => {
        const p = response.predictions[0];
        const { items } = explainByFeature(features, p.prediction, p.contributions);
        setExplanation({
          unitId: unit.id,
          rowId: idColumn ? String(row[idColumn] ?? "") : "",
          items: items.slice(0, 8).map((i) => ({ label: featureLabel(scenario, i.feature), value: i.value })),
        });
      })
      .catch((e: Error) => setError(e.message));
  }

  useCopilotReadable({
    description: `${scenario.title} fleet health on screen: real dataset rows replayed unit by unit and scored by the deployed model. Health is ${health.label}.`,
    value: units.map((u, i) => ({ unit: u.label, status: statuses[i], reading: latest(u) ?? null })),
  });

  if (error && !loaded) return <div className="tab-panel"><div className="error">Could not load the fleet: {error}</div></div>;
  if (!loaded) return <div className="tab-panel"><div className="panel-hint">Loading the fleet…</div></div>;

  return (
    <div className="tab-panel fleet">
      <div className="fleet-head">
        <div>
          <h2>{extras.title}</h2>
          {extras.subtitle && <p className="panel-hint">{extras.subtitle}</p>}
        </div>
        <button className="btn-secondary" onClick={() => setRunning((r) => !r)}>
          {running ? "⏸ Pause replay" : "▶ Resume replay"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="kpi-row fleet-kpis">
        {(Object.keys(STATUS) as Status[]).map((s) => (
          <StatTile key={s} label={`${STATUS[s].glyph} ${STATUS[s].label}`} value={String(count(s))} sub={`of ${units.length} ${extras.unit_noun}s`} highlight={s === "critical" && count(s) > 0} />
        ))}
      </div>

      <div className="fleet-grid-tiles">
        {units.map((u, i) => {
          const status = statuses[i];
          const reading = latest(u);
          const look = status ? STATUS[status] : null;
          return (
            <button key={u.id} className={`fleet-tile${u.id === selectedId ? " fleet-tile--on" : ""}`} onClick={() => select(u)} aria-pressed={u.id === selectedId}>
              <span className="fleet-tile-head">
                <span className="fleet-tile-name" title={u.label}>
                  {u.label}
                </span>
                <span className="fleet-tile-status" style={look ? { color: look.color } : undefined}>
                  {look ? `${look.glyph} ${look.label}` : "Starting…"}
                </span>
              </span>
              <span className="fleet-tile-value">{reading ? healthText(reading.health) : "—"}</span>
              <span className="fleet-tile-label">{health.label}</span>
              <span className="fleet-tile-track">
                <span className="fleet-tile-fill" style={{ width: `${reading ? Math.max(3, severity(reading.health, health) * 100) : 0}%`, background: look?.color }} />
              </span>
              <Sparkline values={u.history.map((r) => r.health)} color={look?.color ?? CHART_COLORS.dim} />
              <span className="fleet-tile-foot" title={u.rowId}>
                {u.pos + 1} of {u.rows.length}
                {!extras.group_column && u.rowId ? ` · ${u.rowId}` : ""}
              </span>
            </button>
          );
        })}
      </div>

      {selected && (
        <div className="grid-2 fleet-detail">
          <div className="panel-card">
            <h3>{selected.label}</h3>
            {selectedReading && (
              <p className="panel-hint">
                Actual {format(selectedReading.actual)}, predicted {format(selectedReading.predicted)}
                {!percent && scenario.target_units ? ` ${scenario.target_units}` : ""}
                {health.value === "residual" ? `, ${health.label.toLowerCase()} ${healthText(selectedReading.health)}` : ""}.
              </p>
            )}
            <ReplayChart history={selected.history} label={scenario.target_label ?? scenario.target ?? "Target"} format={format} />
          </div>
          <div className="panel-card">
            <h3>What drives the model here</h3>
            {explanation?.unitId === selected.id ? (
              <>
                {/* Colour by meaning: more remaining life is good, a higher risk is bad, and a push on a target that is not health (a residual's) is neither. */}
                <BarList
                  items={explanation.items}
                  valueFormatter={(v) => `${v >= 0 ? "+" : ""}${v.toFixed(3)}`}
                  positiveColor={health.value === "residual" ? CHART_COLORS.accent : health.direction === "lower_is_worse" ? CHART_COLORS.green : CHART_COLORS.red}
                  negativeColor={health.value === "residual" ? CHART_COLORS.purple : health.direction === "lower_is_worse" ? CHART_COLORS.red : CHART_COLORS.green}
                />
                <p className="panel-hint">SHAP for the reading you clicked{explanation.rowId ? ` (${explanation.rowId})` : ""}: it does not move with the replay.</p>
              </>
            ) : (
              <p className="panel-hint">Explaining…</p>
            )}
          </div>
        </div>
      )}

      <p className="panel-hint fleet-note">
        {extras.replay_note ? `${extras.replay_note} ` : ""}
        Real rows of this scenario's dataset, one tick every {extras.tick_seconds} s, scored live by the deployed model. That model was trained on these same rows, so it fits them better than it would fit new data. Nothing here reads live telemetry or controls equipment.
      </p>
    </div>
  );
}
