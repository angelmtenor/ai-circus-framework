import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  FLOW_STAGES,
  formatMetric,
  optionLabel,
  type FlowStage,
  type HotSnapshot,
  type PipelineEvent,
  type RecordDetail,
  type SimulatorAction,
} from "./dataApi";
import type { DataProductProps } from "./DataProductView";
import { Icon } from "./Icon";

const DEFAULT_LABELS: Record<FlowStage, string> = {
  produced: "Source",
  event_log: "Event log",
  validated: "Contract",
  bronze: "Bronze",
  hot: "Live tile",
  lake: "Lake",
  transformed: "Transform",
  gold: "Gold",
  served: "Served",
};
const MAX_TOKENS = 36;
const MAX_LOG = 60;
const TOKEN_TTL_MS = 90_000;
const STAGE_INDEX = Object.fromEntries(FLOW_STAGES.map((s, i) => [s, i])) as Record<FlowStage, number>;

type Token = {
  id: string; // `${record_id}|${version}`
  recordId: string;
  entity: string;
  stage: FlowStage;
  status: string;
  lane: number;
  bornAt: number;
  touchedAt: number;
  latencyMs: number | null;
};

function lane(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return Math.abs(h) % 4;
}

function shortId(id: string): string {
  return id.length > 14 ? `…${id.slice(-10)}` : id;
}

/**
 * The live flow of records through the data platform — the showpiece of a data product.
 * Every stage a record passes (data-ingest: event log → contract → bronze → live tile → lake;
 * data-transform: transform → gold; data-api: served) arrives as a pipeline event over SSE and
 * moves that record's token along the track. Quarantined records drop into the tray; a dbt run
 * moves everything waiting in bronze/lake into gold at once (curated by watermark). The
 * Simulate panel fires the data product's declared simulator actions through the real sources.
 */
export function DataFlowView({ scenario, client, overview, refresh }: DataProductProps) {
  const dp = scenario.data_product!;
  const labels = { ...DEFAULT_LABELS, ...(dp.stage_labels as Record<FlowStage, string>) };
  const [tokens, setTokens] = useState<Token[]>([]);
  const [log, setLog] = useState<PipelineEvent[]>([]);
  const [counts, setCounts] = useState<Record<string, number[]>>({});
  const [connected, setConnected] = useState(false);
  const [selected, setSelected] = useState<{ entity: string; key: string } | null>(null);
  const [hot, setHot] = useState<HotSnapshot | null>(overview?.hot ?? null);
  const [now, setNow] = useState(() => Date.now());
  const entities = useMemo(() => new Set(dp.records.map((r) => r.entity)), [dp.records]);

  const onEvent = useCallback(
    (event: PipelineEvent) => {
      const at = Date.now();
      setLog((l) => [event, ...l].slice(0, MAX_LOG));
      setCounts((c) => {
        const stamps = [...(c[event.stage] ?? []).filter((t) => at - t < 60_000)];
        const n = event.entity === "batch" ? Number(event.detail.count ?? 1) : 1;
        for (let i = 0; i < Math.min(n, 500); i++) stamps.push(at);
        return { ...c, [event.stage]: stamps };
      });
      const stage = event.stage as FlowStage;
      if (event.entity === "batch") {
        if (stage === "gold" && event.status === "curated") {
          // A successful dbt run: everything that was waiting below gold is now curated.
          setTokens((ts) =>
            ts.map((t) =>
              t.status !== "quarantined" && STAGE_INDEX[t.stage] < STAGE_INDEX.gold
                ? { ...t, stage: "gold", status: "curated", touchedAt: at }
                : t,
            ),
          );
        }
        return;
      }
      if (!(stage in STAGE_INDEX)) return;
      const version = Number(event.detail?.version ?? 1);
      const id = `${event.record_id}|${version}`;
      setTokens((ts) => {
        const existing = ts.find((t) => t.id === id);
        if (existing) {
          // Never move backwards (events of one record can interleave).
          if (STAGE_INDEX[stage] < STAGE_INDEX[existing.stage] && event.status !== "quarantined") return ts;
          return ts.map((t) =>
            t.id === id ? { ...t, stage, status: event.status, touchedAt: at, latencyMs: event.latency_ms ?? t.latencyMs } : t,
          );
        }
        const token: Token = {
          id,
          recordId: event.record_id,
          entity: event.entity,
          stage,
          status: event.status,
          lane: lane(id),
          bornAt: at,
          touchedAt: at,
          latencyMs: event.latency_ms,
        };
        return [...ts, token].slice(-MAX_TOKENS);
      });
    },
    [],
  );

  // SSE with reconnect; recent history fills the log first (not animated).
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    client
      .events()
      .then((recent) => setLog(recent.slice(-MAX_LOG).reverse()))
      .catch(() => undefined);
    const connect = async () => {
      while (!stopped) {
        try {
          setConnected(true);
          await client.stream(onEvent, controller.signal);
        } catch {
          /* reconnect below */
        }
        setConnected(false);
        if (!stopped) await new Promise((r) => setTimeout(r, 3000));
      }
    };
    void connect();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [client, onEvent]);

  // Clock for fading tokens, and the live tile refresh.
  useEffect(() => {
    const id = window.setInterval(() => {
      setNow(Date.now());
      setTokens((ts) => ts.filter((t) => Date.now() - t.touchedAt < TOKEN_TTL_MS));
    }, 1000);
    return () => window.clearInterval(id);
  }, []);
  const hotName = dp.hot_path[0]?.name;
  useEffect(() => {
    if (!hotName) return;
    let cancelled = false;
    const load = () => client.hot(hotName, 60).then((h) => !cancelled && setHot(h)).catch(() => undefined);
    load();
    const id = window.setInterval(load, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [client, hotName]);

  const quarantined = tokens.filter((t) => t.status === "quarantined");
  const onTrack = tokens.filter((t) => t.status !== "quarantined");
  const pct = (stage: FlowStage) => (STAGE_INDEX[stage] / (FLOW_STAGES.length - 1)) * 100;

  return (
    <div className="tab-panel dp-flow">
      <HotStrip hot={hot} />
      <div className="panel-card dp-track-card">
        <div className="dp-track-head">
          <h4>
            Records through the platform <span className={`dp-dot${connected ? " dp-dot--up" : ""}`} />{" "}
            <span className="panel-hint">{connected ? "live" : "reconnecting…"}</span>
          </h4>
          <span className="panel-hint">
            Curated through{" "}
            {overview?.transform?.curated_through ? new Date(overview.transform.curated_through).toLocaleTimeString() : "—"} · dbt
            every few minutes
          </span>
        </div>
        <div className="dp-track">
          <div className="dp-track-line" />
          {FLOW_STAGES.map((stage) => {
            const recent = (counts[stage] ?? []).filter((t) => now - t < 60_000).length;
            return (
              <div key={stage} className={`dp-station dp-station--${stage}`} style={{ left: `${pct(stage)}%` }}>
                <div className={`dp-station-dot${recent ? " dp-station-dot--busy" : ""}`} />
                <div className="dp-station-label">{labels[stage]}</div>
                <div className="dp-station-count">{recent ? `${recent}/min` : "·"}</div>
              </div>
            );
          })}
          {onTrack.map((t) => (
            <button
              key={t.id}
              className={`dp-token dp-token--${t.status}`}
              style={{ left: `${pct(t.stage)}%`, top: `${74 + t.lane * 22}px`, opacity: Math.max(0.25, 1 - (now - t.touchedAt) / TOKEN_TTL_MS) }}
              title={`${t.entity} ${t.recordId} — ${t.stage} (${t.status})${t.latencyMs != null ? `, ${t.latencyMs} ms after production` : ""}`}
              onClick={() => entities.has(t.entity) && setSelected({ entity: t.entity, key: t.recordId })}
            >
              {shortId(t.recordId)}
            </button>
          ))}
          <div className="dp-quarantine-tray" style={{ left: `${pct("validated")}%` }}>
            <div className="dp-tray-label">
              <Icon name="shield" size={12} /> Quarantine
            </div>
            {quarantined.slice(-6).map((t) => (
              <button key={t.id} className="dp-token dp-token--quarantined dp-token--static" onClick={() => setSelected({ entity: t.entity, key: t.recordId })}>
                {shortId(t.recordId)}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="dp-flow-columns">
        <SimulatePanel actions={dp.simulator?.actions ?? []} client={client} overview={overview} refresh={refresh} onRecord={setSelected} entities={entities} />
        <div className="panel-card dp-log">
          <h4>Pipeline events</h4>
          <div className="dp-log-lines">
            {log.length === 0 && <p className="panel-hint">Nothing yet — fire an action, or turn live mode on.</p>}
            {log.map((e, i) => (
              <div
                key={`${e.ts}-${i}`}
                className={`dp-log-line dp-log-line--${e.status}`}
                onClick={() => e.entity !== "batch" && entities.has(e.entity) && setSelected({ entity: e.entity, key: e.record_id })}
              >
                <span className="dp-log-time">{new Date(e.ts).toLocaleTimeString()}</span>
                <span className="dp-log-stage">{labels[e.stage as FlowStage] ?? e.stage}</span>
                <span className="dp-log-status">{e.status}</span>
                <span className="dp-log-id">{e.entity === "batch" ? `${e.detail.count ?? ""} records${e.detail.message ? ` · ${e.detail.message}` : ""}` : e.record_id}</span>
                {e.latency_ms != null && <span className="dp-log-latency">{(e.latency_ms / 1000).toFixed(1)} s</span>}
              </div>
            ))}
          </div>
        </div>
      </div>
      {selected && <RecordDrawer client={client} entity={selected.entity} recordKey={selected.key} onClose={() => setSelected(null)} />}
    </div>
  );
}

function Sparkline({ values, height = 38 }: { values: number[]; height?: number }) {
  if (values.length < 2) return <div style={{ height }} />;
  const max = Math.max(...values, 1);
  const w = 100 / (values.length - 1);
  const points = values.map((v, i) => `${(i * w).toFixed(2)},${(height - (v / max) * (height - 4) - 2).toFixed(2)}`).join(" ");
  return (
    <svg className="dp-spark" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" style={{ height }}>
      <polyline points={`0,${height} ${points} 100,${height}`} className="dp-spark-fill" />
      <polyline points={points} className="dp-spark-line" />
    </svg>
  );
}

function HotStrip({ hot }: { hot: HotSnapshot | null }) {
  if (!hot) return null;
  return (
    <div className="dp-hot">
      {hot.measures.map((m) => {
        const money = m.endsWith("_cents");
        const series = hot.series.map((r) => Number(r[m] ?? 0));
        return (
          <div className="kpi-tile dp-hot-tile" key={m}>
            <div className="kpi-label">
              {m.replace(/_cents$/, "").replace(/_/g, " ")} today <span className="dp-approx">live · approximate</span>
            </div>
            <div className="kpi-value">{formatMetric(Number(hot.today[m] ?? 0), money ? "currency_cents" : "integer")}</div>
            <Sparkline values={series} />
            <div className="kpi-sub">last 60 min, per minute</div>
          </div>
        );
      })}
    </div>
  );
}

function SimulatePanel({
  actions,
  client,
  overview,
  refresh,
  onRecord,
  entities,
}: {
  actions: SimulatorAction[];
  entities: Set<string>;
  client: DataProductProps["client"];
  overview: DataProductProps["overview"];
  refresh: () => void;
  onRecord: (r: { entity: string; key: string }) => void;
}) {
  const sim = overview?.simulator;
  const [values, setValues] = useState<Record<string, Record<string, string | number | boolean>>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const live = sim?.live ?? false;
  const run = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    setMessage(null);
    try {
      const result = (await fn()) as Record<string, unknown> | undefined;
      if (result && typeof result.record_id === "string" && typeof result.entity === "string") {
        setMessage(`${result.entity} ${result.record_id}${result.note ? ` — ${result.note}` : ""}`);
        if (entities.has(result.entity)) onRecord({ entity: result.entity, key: result.record_id });
      }
      refresh();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  if (!sim) {
    return (
      <div className="panel-card dp-sim">
        <h4>
          <Icon name="sparkle" /> Simulate
        </h4>
        <p className="panel-hint">Simulation writes data: available to the platform admin, or to a tenant on its own data product.</p>
      </div>
    );
  }
  const paramValue = (action: SimulatorAction, name: string) => {
    const p = action.params.find((x) => x.name === name)!;
    return values[action.name]?.[name] ?? p.default ?? (p.type === "bool" ? false : p.type === "int" ? 0 : p.options[0]);
  };
  return (
    <div className="panel-card dp-sim">
      <h4>
        <Icon name="sparkle" /> Simulate the sources
      </h4>
      <div className="dp-live-row">
        <button className={`dp-btn${live ? " dp-btn--on" : ""}`} disabled={busy !== null} onClick={() => run("live", () => client.live(!live, 30))}>
          <Icon name="pulse" size={14} /> {live ? "Stop live trading" : "Start live trading (30 min)"}
        </button>
        {live && <span className="panel-hint">{sim.live_produced} records so far</span>}
        {sim.backfill.state === "running" && <span className="panel-hint">history: {sim.backfill.progress?.at(-1) ?? "starting…"}</span>}
        {sim.backfill.state === "idle" && !overview?.transform && (
          <button className="dp-btn" disabled={busy !== null} onClick={() => run("backfill", () => client.backfill())}>
            Generate history
          </button>
        )}
      </div>
      <div className="dp-actions">
        {actions.map((action) => (
          <div key={action.name} className={`dp-action dp-action--${action.tone}`}>
            <div className="dp-action-head">
              <b>{action.label}</b>
              <button
                className="dp-btn dp-btn--small"
                disabled={busy !== null}
                onClick={() =>
                  run(action.name, () =>
                    client.simulate(action.name, Object.fromEntries(action.params.map((p) => [p.name, paramValue(action, p.name)]))),
                  )
                }
              >
                {busy === action.name ? "…" : "Run"}
              </button>
            </div>
            <div className="panel-hint">{action.description}</div>
            {action.params.length > 0 && (
              <div className="dp-action-params">
                {action.params.map((p) => (
                  <label key={p.name}>
                    {p.label}{" "}
                    {p.type === "choice" ? (
                      <select
                        value={String(paramValue(action, p.name))}
                        onChange={(e) => setValues((v) => ({ ...v, [action.name]: { ...v[action.name], [p.name]: e.target.value } }))}
                      >
                        {p.options.map((o) => (
                          <option key={o} value={o}>
                            {p.name === overview?.row_filter?.column ? optionLabel(o, overview?.row_filter_options) : o}
                          </option>
                        ))}
                      </select>
                    ) : p.type === "bool" ? (
                      <input
                        type="checkbox"
                        checked={Boolean(paramValue(action, p.name))}
                        onChange={(e) => setValues((v) => ({ ...v, [action.name]: { ...v[action.name], [p.name]: e.target.checked } }))}
                      />
                    ) : (
                      <input
                        type="number"
                        min={p.min ?? undefined}
                        max={p.max ?? undefined}
                        value={Number(paramValue(action, p.name))}
                        onChange={(e) => setValues((v) => ({ ...v, [action.name]: { ...v[action.name], [p.name]: Number(e.target.value) } }))}
                      />
                    )}
                  </label>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      {message && <div className="dp-sim-message">{message}</div>}
    </div>
  );
}

const TRAIL: { key: keyof NonNullable<RecordDetail["current"]>["trail"]; label: string }[] = [
  { key: "produced", label: "Produced at the source" },
  { key: "event_log", label: "Event log (Kafka)" },
  { key: "validated", label: "Contract + rules passed" },
  { key: "bronze", label: "Bronze (raw, by key)" },
  { key: "lake", label: "Iceberg lake" },
  { key: "gold", label: "Curated into gold (watermark)" },
];

/** One record with its processing trail — opened from the track, the log or a simulate action. */
export function RecordDrawer({
  client,
  entity,
  recordKey,
  onClose,
}: {
  client: DataProductProps["client"];
  entity: string;
  recordKey: string;
  onClose: () => void;
}) {
  const [record, setRecord] = useState<RecordDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      client
        .record(entity, recordKey)
        .then((r) => !cancelled && (setRecord(r), setError(null)))
        .catch((e: Error) => !cancelled && setError(e.message));
    load();
    timer.current = window.setInterval(load, 5000); // watch it move through the stages
    return () => {
      cancelled = true;
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [client, entity, recordKey]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const current = record?.current;
  const origin = current?.trail.produced ? new Date(current.trail.produced).getTime() : null;
  return (
    <div className="dp-drawer-overlay" onClick={onClose}>
      <div className="dp-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="dp-drawer-head">
          <h4>
            {record?.label ?? entity} <code>{recordKey}</code>
          </h4>
          <button className="chat-dock-close" onClick={onClose}>
            <Icon name="close" size={14} />
          </button>
        </div>
        {error && !record && (
          <p className="panel-hint">
            {error.startsWith("No ") ? "On its way — not in bronze yet (this view refreshes every few seconds)." : error}
          </p>
        )}
        {record && (
          <>
            {record.quarantined.length > 0 && (
              <div className="dp-quarantine-box">
                <b>Quarantined {record.quarantined.length}×</b>
                {record.quarantined.map((q) => (
                  <ul key={q.quarantine_id}>
                    {q.violations.map((v) => (
                      <li key={v.rule}>
                        <code>{v.rule}</code> {v.message}
                      </li>
                    ))}
                    <li className="panel-hint">status: {q.status}</li>
                  </ul>
                ))}
              </div>
            )}
            {current && (
              <>
                <ol className="dp-trail">
                  {TRAIL.map((step) => {
                    const at = current.trail[step.key];
                    const delta = at && origin ? (new Date(at).getTime() - origin) / 1000 : null;
                    return (
                      <li key={step.key} className={at ? "dp-trail--done" : ""}>
                        <span className="dp-trail-dot" />
                        <span className="dp-trail-label">{step.label}</span>
                        <span className="dp-trail-time">
                          {at ? new Date(at).toLocaleTimeString() : "waiting…"}
                          {delta !== null && delta >= 0 && step.key !== "produced" && <em> +{delta < 120 ? `${delta.toFixed(1)} s` : `${(delta / 60).toFixed(1)} min`}</em>}
                        </span>
                      </li>
                    );
                  })}
                </ol>
                <div className="scenario-meta-row">
                  {Object.entries(current.summary).map(([k, v]) => (
                    <span className="scenario-meta-pill" key={k}>
                      {k}: {k.toLowerCase().includes("total") ? formatMetric(Number(v), "currency_cents") : String(v ?? "—")}
                    </span>
                  ))}
                  <span className="scenario-meta-pill">source: {current.source_kind}</span>
                  {record.versions.length > 1 && <span className="scenario-meta-pill">{record.versions.length} versions</span>}
                  {current.corrections > 0 && <span className="scenario-meta-pill">{current.corrections} corrections</span>}
                </div>
                <details open>
                  <summary>Payload as received (masked for your role)</summary>
                  <pre className="dp-json">{JSON.stringify(current.payload, null, 2)}</pre>
                </details>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
