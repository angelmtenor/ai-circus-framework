import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { config } from "./config";
import { formatMetric, optionLabel, type MetricResult, type PipelineEvent } from "./dataApi";
import type { DataProductProps } from "./DataProductView";
import { CAPABILITIES, DEPTH_LABELS, TOUR, type MapNode, type TourAction, type TourDepth, type TourStep } from "./dataTour";
import { Icon } from "./Icon";

/* ── the platform map ───────────────────────────────────────────────────────────── */

type NodeSpec = { x: number; y: number; label: string; sub: string; glyph: string; band: string };

const W = 1180;
const H = 520;
const NODES: Record<MapNode, NodeSpec> = {
  src_stream: { x: 70, y: 110, label: "Live sources", sub: "events", glyph: "⚡", band: "source" },
  src_files: { x: 70, y: 255, label: "Daily files", sub: "batch", glyph: "🗂", band: "source" },
  src_db: { x: 70, y: 400, label: "Business DB", sub: "tables", glyph: "🗄", band: "source" },
  kafka: { x: 235, y: 110, label: "Event log", sub: "Kafka", glyph: "≋", band: "capture" },
  landing: { x: 235, y: 255, label: "Landing", sub: "object storage", glyph: "⇣", band: "capture" },
  cdc: { x: 235, y: 400, label: "Change capture", sub: "CDC", glyph: "↻", band: "capture" },
  contract: { x: 405, y: 185, label: "Contract gate", sub: "schema + rules", glyph: "✓", band: "quality" },
  quarantine: { x: 405, y: 330, label: "Quarantine", sub: "fix & replay", glyph: "⚠", band: "quality" },
  hot: { x: 575, y: 70, label: "Live tile", sub: "seconds", glyph: "◔", band: "bronze" },
  bronze: { x: 575, y: 210, label: "Bronze", sub: "raw, by key", glyph: "▤", band: "bronze" },
  lake: { x: 575, y: 360, label: "Lake", sub: "Iceberg / Parquet", glyph: "◇", band: "bronze" },
  dbt: { x: 740, y: 210, label: "Transform", sub: "dbt + tests", glyph: "ƒ", band: "silver" },
  silver: { x: 880, y: 140, label: "Silver", sub: "clean model", glyph: "▥", band: "silver" },
  gold: { x: 880, y: 290, label: "Gold", sub: "marts + metrics", glyph: "★", band: "gold" },
  api: { x: 1080, y: 62, label: "Governed API", sub: "REST · SSE", glyph: "⌁", band: "serve" },
  dashboards: { x: 1080, y: 145, label: "Dashboards", sub: "native + BI", glyph: "▦", band: "serve" },
  sql: { x: 1080, y: 228, label: "SQL", sub: "read-only", glyph: "⌨", band: "serve" },
  assistant: { x: 1080, y: 311, label: "Assistant", sub: "ask in words", glyph: "✦", band: "serve" },
  models: { x: 1080, y: 400, label: "Models", sub: "forecast · propensity", glyph: "◎", band: "serve" },
  catalog: { x: 880, y: 420, label: "Catalogue", sub: "lineage · owners", glyph: "⌘", band: "govern" },
};
const EDGES: Edge[] = [
  ["src_stream", "kafka"],
  ["src_files", "landing"],
  ["src_db", "cdc"],
  ["kafka", "contract"],
  ["landing", "contract"],
  ["contract", "quarantine"],
  ["contract", "bronze"],
  ["cdc", "bronze"],
  ["bronze", "hot"],
  ["bronze", "lake"],
  ["bronze", "dbt"],
  ["dbt", "silver"],
  ["silver", "gold"],
  ["hot", "dashboards"],
  ["gold", "api"],
  ["gold", "dashboards"],
  ["gold", "sql"],
  ["gold", "assistant"],
  ["gold", "catalog"],
  ["lake", "catalog"],
  ["gold", "models"],
  ["models", "assistant"],
];
// Which edge a pipeline event travels on — real records light the map up.
const STAGE_EDGE: Record<string, Edge> = {
  event_log: ["src_stream", "kafka"],
  validated: ["kafka", "contract"],
  bronze: ["contract", "bronze"],
  hot: ["bronze", "hot"],
  lake: ["bronze", "lake"],
  transformed: ["bronze", "dbt"],
  gold: ["silver", "gold"],
  served: ["gold", "api"],
};

const edgeId = ([a, b]: Edge) => `dp-edge-${a}-${b}`;

function edgePath([a, b]: Edge): string {
  const p = NODES[a];
  const q = NODES[b];
  const dx = Math.max(40, Math.abs(q.x - p.x) / 2);
  return `M${p.x + 46},${p.y} C${p.x + 46 + dx},${p.y} ${q.x - 46 - dx},${q.y} ${q.x - 46},${q.y}`;
}

type Burst = { id: number; edge: Edge; tone: string };

function BurstDot({ burst }: { burst: Burst }) {
  const ref = useRef<SVGAnimateMotionElement | null>(null);
  useEffect(() => {
    // SMIL `begin` is relative to the document's start: begin it now, explicitly.
    ref.current?.beginElement();
  }, []);
  return (
    <circle r={6} className={`dp-burst dp-burst--${burst.tone}`}>
      <animateMotion ref={ref} dur="1.1s" begin="indefinite" fill="freeze" rotate="auto">
        <mpath href={`#${edgeId(burst.edge)}`} />
      </animateMotion>
    </circle>
  );
}

function PlatformMap({
  active,
  bursts,
  counts,
  tiers,
  onPick,
}: {
  active: Set<MapNode>;
  bursts: Burst[];
  counts: Partial<Record<MapNode, string>>;
  tiers: Record<string, boolean>;
  onPick: (node: MapNode) => void;
}) {
  const focus = active.size > 0;
  const off = (node: MapNode) => (node === "catalog" && !tiers.governance ? " dp-map-node--off" : "");
  return (
    <svg className="dp-map" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="The data platform, from sources to the people who use the data">
      <defs>
        <linearGradient id="dp-flow-grad" x1="0" x2="1">
          <stop offset="0%" stopColor="var(--blue)" />
          <stop offset="55%" stopColor="var(--accent)" />
          <stop offset="100%" stopColor="var(--amber)" />
        </linearGradient>
        <filter id="dp-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="4" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      {[
        ["SOURCES", 70],
        ["CAPTURE", 235],
        ["QUALITY", 405],
        ["STORE", 575],
        ["TRANSFORM", 810],
        ["SERVE", 1080],
      ].map(([label, x]) => (
        <text key={label} x={x} y={22} className="dp-map-band" textAnchor="middle">
          {label}
        </text>
      ))}
      <rect x={20} y={478} width={W - 40} height={30} rx={10} className="dp-map-govern" />
      <text x={W / 2} y={498} className="dp-map-govern-label" textAnchor="middle">
        GOVERNANCE, ACROSS EVERY STEP · identity · tenancy · roles · row filters · masks · audit · lineage
      </text>
      {EDGES.map((edge, i) => {
        const on = !focus || (active.has(edge[0]) && active.has(edge[1])) || (active.has(edge[1]) && !focus);
        const lit = focus && active.has(edge[0]) && active.has(edge[1]);
        return (
          <g key={edgeId(edge)} className={`dp-map-edge${lit ? " dp-map-edge--lit" : ""}${focus && !lit ? " dp-map-edge--dim" : ""}`}>
            <path id={edgeId(edge)} d={edgePath(edge)} />
            {on && (
              <circle r={2.6} className="dp-map-ambient">
                <animateMotion dur={`${3.2 + (i % 5) * 0.4}s`} begin={`${(i * 0.37) % 3}s`} repeatCount="indefinite">
                  <mpath href={`#${edgeId(edge)}`} />
                </animateMotion>
              </circle>
            )}
          </g>
        );
      })}
      {bursts.map((b) => (
        <BurstDot key={b.id} burst={b} />
      ))}
      {(Object.keys(NODES) as MapNode[]).map((key) => {
        const n = NODES[key];
        const lit = active.has(key);
        return (
          <g
            key={key}
            transform={`translate(${n.x},${n.y})`}
            className={`dp-map-node dp-map-node--${n.band}${lit ? " dp-map-node--lit" : ""}${focus && !lit ? " dp-map-node--dim" : ""}${off(key)}`}
            onClick={() => onPick(key)}
          >
            <rect x={-46} y={-30} width={92} height={60} rx={12} filter={lit ? "url(#dp-glow)" : undefined} />
            <text y={-6} className="dp-map-glyph" textAnchor="middle">
              {n.glyph}
            </text>
            <text y={12} className="dp-map-label" textAnchor="middle">
              {n.label}
            </text>
            <text y={24} className="dp-map-sub" textAnchor="middle">
              {counts[key] ?? n.sub}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ── the tour ───────────────────────────────────────────────────────────────────── */

/** Step text with `backticked` names rendered as code. */
function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split("`").map((part, i) => (i % 2 ? <code key={i}>{part}</code> : <span key={i}>{part}</span>))}
    </>
  );
}

const AUTOPLAY_MS = 11_000;

/**
 * The guided tour of the data platform — the first thing a data product shows. A live map of
 * the whole platform (sources → capture → quality → bronze / lake / live tile → transform →
 * silver → gold → every door, under one governance band) that lights up with real pipeline
 * events, and eleven steps that walk it at three depths (Simple · Detailed · Engineer), each
 * with live proof from this cluster and a "try it" that does the real thing. Generic: the
 * steps are platform capabilities, filled with the data product's own sources, metrics and roles.
 */
export function DataTourView(props: DataProductProps) {
  const { scenario, client, overview, refresh, navigate, onViewAs, openChat } = props;
  const dp = scenario.data_product!;
  const [index, setIndex] = useState(0);
  const [depth, setDepth] = useState<TourDepth>(() => (localStorage.getItem("dp-tour-depth") as TourDepth) || "simple");
  const [playing, setPlaying] = useState(false);
  const [bursts, setBursts] = useState<Burst[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [metric, setMetric] = useState<MetricResult | null>(null);
  const counter = useRef(0);
  const step = TOUR[index];

  useEffect(() => {
    try {
      localStorage.setItem("dp-tour-depth", depth);
    } catch {
      /* private mode: the default depth is fine */
    }
  }, [depth]);

  // Real pipeline events become bursts on the map.
  const onEvent = useCallback((event: PipelineEvent) => {
    const edge = event.status === "quarantined" ? (["contract", "quarantine"] as Edge) : STAGE_EDGE[event.stage];
    if (!edge) return;
    const tone = event.status === "quarantined" ? "bad" : event.stage === "gold" ? "gold" : event.status === "duplicate" ? "dup" : "ok";
    const id = ++counter.current;
    setBursts((b) => [...b.slice(-40), { id, edge, tone }]);
    window.setTimeout(() => setBursts((b) => b.filter((x) => x.id !== id)), 1400);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    const run = async () => {
      while (!stopped) {
        try {
          await client.stream(onEvent, controller.signal);
        } catch {
          /* reconnect */
        }
        if (!stopped) await new Promise((r) => setTimeout(r, 3000));
      }
    };
    void run();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [client, onEvent]);

  // A "try it" message belongs to its step.
  useEffect(() => setMessage(null), [index]);

  useEffect(() => {
    if (!playing) return;
    const id = window.setTimeout(() => setIndex((i) => (i + 1) % TOUR.length), AUTOPLAY_MS);
    return () => window.clearTimeout(id);
  }, [playing, index]);

  useEffect(() => {
    if (step.proof !== "metric" && step.proof !== "doors") return;
    const name = dp.kpis[0] ?? dp.metrics[0]?.name;
    if (!name) return;
    client.metric(name, { period: "today", compare: "same_period_last_week" }).then(setMetric).catch(() => setMetric(null));
  }, [client, step.proof, dp.kpis, dp.metrics]);

  const fill = (text: string) => {
    const rules = [...new Map(dp.sources.flatMap((s) => s.rules ?? []).map((r) => [r.description, r])).values()];
    const roles = Object.values(dp.access.roles).map((r) => r.label);
    const firstMetric = dp.metrics.find((m) => m.name === dp.kpis[0]) ?? dp.metrics[0];
    return text
      .replace("{sourceCount}", String(dp.sources.length))
      .replace("{ruleCount}", String(rules.length))
      .replace("{firstRule}", rules[0]?.description ?? "totals add up")
      .replace("{metricLabel}", firstMetric?.label ?? "revenue")
      .replace("{roleList}", roles.join(", "));
  };

  const capture = overview?.capture ?? {};
  const total = (k: "records" | "in_lake" | "corrections") =>
    Object.values(capture).reduce((sum, row) => sum + Number(row[k] ?? 0), 0);
  const counts: Partial<Record<MapNode, string>> = {
    bronze: overview?.capture ? `${total("records").toLocaleString()} records` : undefined,
    lake: overview?.capture ? `${total("in_lake").toLocaleString()} archived` : undefined,
    quarantine: overview?.ops.quarantine ? `${overview.ops.quarantine.open} open` : undefined,
    hot: overview?.hot ? formatMetric(Number(overview.hot.today[overview.hot.measures.find((m) => m.endsWith("_cents")) ?? ""] ?? 0), "currency_cents") : undefined,
    gold: overview?.transform?.curated_through ? `fresh ${new Date(overview.transform.curated_through).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : undefined,
  };

  const filterRoleName = Object.entries(dp.access.roles).find(([, r]) => r.rows === "filtered")?.[0];
  const filterExample =
    dp.simulator?.actions.flatMap((a) => a.params).find((p) => p.name === dp.access.row_filter?.column)?.default ?? null;

  const act = async (action: TourAction) => {
    setMessage(null);
    if (action.kind === "tab") return navigate?.(action.tab);
    if (action.kind === "chat") return openChat?.();
    if (action.kind === "viewAs") {
      if (filterRoleName && onViewAs) {
        onViewAs({ role: filterRoleName, values: filterExample ? [String(filterExample)] : [] });
        setMessage(`Now viewing as ${dp.access.roles[filterRoleName].label}${filterExample ? ` (${filterExample})` : ""} — every tab, query and answer is restricted. Switch back with “View as: myself”.`);
      }
      return;
    }
    setBusy(true);
    try {
      const result = await client.simulate(action.action, action.params ?? {});
      const id = String(result.record_id ?? "");
      setMessage(
        action.action === "sell_bad"
          ? `Sent ${id} — watch it stop at the contract gate and drop into quarantine.`
          : action.action === "duplicate"
            ? `Re-sent ${id} — bronze recognises the fingerprint: the count does not change.`
            : `Rang up ${id} — watch it flow from the till into bronze, the lake and the live tile.`,
      );
      window.setTimeout(refresh, 2500);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const active = useMemo(() => new Set<MapNode>(step.nodes), [step.nodes]);
  const pick = (node: MapNode) => {
    const at = TOUR.findIndex((s) => s.nodes[0] === node);
    const any = at >= 0 ? at : TOUR.findIndex((s) => s.nodes.includes(node));
    if (any >= 0) {
      setPlaying(false);
      setIndex(any);
    }
  };
  const tiers = overview?.tiers ?? { core: true };

  return (
    <div className="tab-panel dp-tour">
      <div className="panel-card dp-tour-hero">
        <div className="dp-tour-head">
          <div>
            <h3>How a sale becomes trusted data</h3>
            <p className="panel-hint">
              Follow one record from the moment it happens to the dashboard, the assistant and the catalogue. Click any box, or press play.
            </p>
          </div>
          <div className="dp-tour-controls">
            <div className="dp-segmented" role="radiogroup" aria-label="Level of detail">
              {(Object.keys(DEPTH_LABELS) as TourDepth[]).map((d) => (
                <button key={d} className={depth === d ? "active" : ""} onClick={() => setDepth(d)} role="radio" aria-checked={depth === d}>
                  {DEPTH_LABELS[d]}
                </button>
              ))}
            </div>
            <button className={`dp-btn${playing ? " dp-btn--on" : ""}`} onClick={() => setPlaying((p) => !p)}>
              {playing ? "❚❚ Pause" : "▶ Play the tour"}
            </button>
          </div>
        </div>
        <PlatformMap active={active} bursts={bursts} counts={counts} tiers={tiers} onPick={pick} />
        <div className="dp-tour-dots">
          {TOUR.map((s, i) => (
            <button
              key={s.key}
              className={`dp-tour-dot${i === index ? " dp-tour-dot--on" : ""}${i < index ? " dp-tour-dot--done" : ""}`}
              onClick={() => {
                setPlaying(false);
                setIndex(i);
              }}
              title={s.title}
            >
              <span>{i + 1}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="panel-card dp-tour-step" key={step.key}>
        <div className="dp-tour-step-head">
          <span className="dp-tour-chapter">{step.chapter}</span>
          <h3>{step.title}</h3>
          {playing && <span className="dp-tour-progress" style={{ animationDuration: `${AUTOPLAY_MS}ms` }} />}
        </div>
        <div className={`dp-tour-body${step.proof === "capabilities" ? " dp-tour-body--wide" : ""}`}>
          <div className="dp-tour-text">
            <p className="dp-tour-simple">
              <Rich text={fill(step.simple)} />
            </p>
            {depth !== "simple" && (
              <p>
                <Rich text={fill(step.detailed)} />
              </p>
            )}
            {depth === "engineer" && (
              <p className="dp-tour-engineer">
                <Rich text={fill(step.engineer)} />
              </p>
            )}
            {depth !== "simple" && step.components.length > 0 && (
              <div className="dp-tour-components">
                {step.components.map((c) => (
                  <span key={c.name} className={`dp-tour-component${tiers[c.tier] ? "" : " dp-tour-component--off"}`} title={`${c.tier} tier${tiers[c.tier] ? "" : " — not running"}`}>
                    <b>{c.name}</b> {c.role}
                  </span>
                ))}
              </div>
            )}
            {depth === "engineer" && step.alternatives && (
              <p className="panel-hint">
                <b>Interchangeable with:</b> {step.alternatives}
              </p>
            )}
            {step.actions && step.actions.length > 0 && (
              <div className="dp-tour-actions">
                {step.actions.map((a) => (
                  <button key={a.label} className={`dp-btn${a.kind === "simulate" ? " dp-tour-try" : " dp-btn--ghost"}`} disabled={busy} onClick={() => act(a)}>
                    {a.kind === "simulate" ? "▶ " : ""}
                    {a.label}
                  </button>
                ))}
              </div>
            )}
            {message && <div className="dp-sim-message">{message}</div>}
          </div>
          <div className="dp-tour-proof">
            <ProofPanel step={step} depth={depth} {...props} metric={metric} filterRole={filterRoleName} />
          </div>
        </div>
        <div className="dp-tour-nav">
          <button className="dp-btn dp-btn--ghost" disabled={index === 0} onClick={() => setIndex((i) => Math.max(0, i - 1))}>
            ← Back
          </button>
          <span className="panel-hint">
            {index + 1} / {TOUR.length}
          </span>
          <button className="dp-btn" onClick={() => setIndex((i) => (i + 1) % TOUR.length)}>
            {index === TOUR.length - 1 ? "Start again" : "Next →"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── live proof per step ─────────────────────────────────────────────────────────── */

function Fact({ value, label }: { value: string; label: string }) {
  return (
    <div className="dp-fact">
      <div className="dp-fact-value">{value}</div>
      <div className="dp-fact-label">{label}</div>
    </div>
  );
}

function ProofPanel({
  step,
  depth,
  scenario,
  overview,
  metric,
  filterRole,
}: DataProductProps & { step: TourStep; depth: TourDepth; metric: MetricResult | null; filterRole?: string }) {
  const dp = scenario.data_product!;
  const capture = overview?.capture ?? {};
  const sum = (k: "records" | "in_lake" | "corrections") => Object.values(capture).reduce((s, r) => s + Number(r[k] ?? 0), 0);
  const label = "Live, from this cluster";
  switch (step.proof) {
    case "sources":
      return (
        <>
          <h5>{label}</h5>
          {dp.sources.map((s) => (
            <div key={s.id} className="dp-proof-row">
              <span className="dp-role-pill dp-role-pill--muted">{s.type === "kafka" ? "stream" : s.type === "files" ? "files" : "database"}</span> {s.label}
              {depth === "engineer" && <code>{s.topic ?? s.prefix ?? s.tables?.map((t) => t.name).join(", ")}</code>}
            </div>
          ))}
        </>
      );
    case "capture":
    case "bronze":
      return (
        <>
          <h5>{label}</h5>
          <div className="dp-facts">
            <Fact value={sum("records").toLocaleString()} label="records in bronze" />
            <Fact value={sum("corrections").toLocaleString()} label="corrections absorbed" />
            <Fact value={String(overview?.ops.deliveries?.reconciled ?? "—")} label="files reconciled" />
          </div>
          {Object.entries(capture).map(([source, row]) => (
            <div key={source} className="dp-proof-row">
              <code>{source}</code> {Number(row.records).toLocaleString()} · last {row.latest ? new Date(String(row.latest)).toLocaleTimeString() : "—"}
            </div>
          ))}
        </>
      );
    case "quarantine": {
      // Two sources may share a rule (same description): list each once.
      const rules = [...new Map(dp.sources.flatMap((s) => s.rules ?? []).map((r) => [r.description, r])).values()];
      return (
        <>
          <h5>{label}</h5>
          <div className="dp-facts">
            <Fact value={String(overview?.ops.quarantine?.open ?? "—")} label="records in quarantine" />
            <Fact value={String(rules.length)} label="business rules" />
          </div>
          <ul className="dp-rules">
            {rules.slice(0, depth === "simple" ? 3 : 8).map((r) => (
              <li key={r.name}>
                {r.description}
                {depth === "engineer" && <code> {r.name}</code>}
              </li>
            ))}
          </ul>
        </>
      );
    }
    case "hot": {
      const hot = overview?.hot;
      return (
        <>
          <h5>{label}</h5>
          <div className="dp-facts">
            {hot?.measures.map((m) => (
              <Fact key={m} value={formatMetric(Number(hot.today[m] ?? 0), m.endsWith("_cents") ? "currency_cents" : "integer")} label={`${m.replace(/_cents$/, "").replace(/_/g, " ")} today (approx.)`} />
            ))}
          </div>
        </>
      );
    }
    case "lake":
      return (
        <>
          <h5>{label}</h5>
          <div className="dp-facts">
            <Fact value={sum("in_lake").toLocaleString()} label="records in the lake" />
            <Fact value={`${Math.round((100 * sum("in_lake")) / Math.max(1, sum("records")))}%`} label="of bronze archived" />
          </div>
          {depth === "engineer" && (
            <pre className="dp-json">{`-- from any laptop, no platform needed
INSTALL iceberg; LOAD iceberg;
SELECT count(*) FROM iceberg_scan(
  's3://ai-circus-data/lake/${scenario.slug}_bronze/${dp.sources[0]?.id}');`}</pre>
          )}
        </>
      );
    case "transform": {
      const t = overview?.transform;
      return (
        <>
          <h5>{label}</h5>
          <div className="dp-facts">
            <Fact value={t ? String(t.models_ok) : "—"} label="models built" />
            <Fact value={t ? `${t.tests_passed}/${t.tests_passed + t.tests_failed}` : "—"} label="tests passing" />
            <Fact value={t?.curated_through ? new Date(t.curated_through).toLocaleTimeString() : "—"} label="curated through" />
          </div>
          <p className="panel-hint">
            Last run: {t?.status ?? "not yet"} on {t?.target ?? "postgres"}
            {overview?.tiers?.analytics && (
              <>
                {" "}·{" "}
                <a href={config.dagsterUrl} target="_blank" rel="noreferrer">
                  open Dagster <Icon name="external" size={11} />
                </a>
              </>
            )}
          </p>
        </>
      );
    }
    case "metric":
    case "doors": {
      const spec = dp.metrics.find((m) => m.name === metric?.metric);
      return (
        <>
          <h5>{label}</h5>
          {metric && spec ? (
            <div className="dp-facts">
              <Fact value={formatMetric(metric.total, spec.format)} label={`${spec.label} today`} />
              <Fact value={metric.compare?.change_pct != null ? `${metric.compare.change_pct > 0 ? "+" : ""}${metric.compare.change_pct}%` : "—"} label="vs same day last week" />
              <Fact value={overview?.marts_engine ?? "—"} label="marts engine" />
            </div>
          ) : (
            <p className="panel-hint">The marts are being built…</p>
          )}
          {depth === "engineer" && spec && (
            <pre className="dp-json">{`${spec.name} = ${spec.expression}\n  over ${spec.dataset}\n  by ${spec.dimensions.join(", ")}`}</pre>
          )}
          {step.proof === "doors" && (
            <div className="dp-proof-doors">
              <span className="dp-tour-component">
                <b>API</b> /v1/{scenario.slug}/metrics/{metric?.metric}
              </span>
              <span className={`dp-tour-component${overview?.tiers?.bi ? "" : " dp-tour-component--off"}`}>
                <b>Superset</b> {overview?.tiers?.bi ? <a href={config.supersetUrl} target="_blank" rel="noreferrer">open</a> : "bi tier off"}
              </span>
              <span className="dp-tour-component">
                <b>Assistant</b> same metric, same number
              </span>
            </div>
          )}
        </>
      );
    }
    case "models": {
      const exports = dp.ml_exports ?? [];
      const shop = dp.apps?.storefront;
      return (
        <>
          <h5>{label}</h5>
          {exports.length === 0 && <p className="panel-hint">This data product trains no model yet.</p>}
          <div className="dp-proof-doors">
            {exports.map((e) => (
              <span key={e.scenario} className="dp-tour-component">
                <b>{e.scenario.replaceAll("_", " ")}</b> trained on gold <code>{e.dataset}</code>
              </span>
            ))}
            {shop?.recommendations && (
              <span className="dp-tour-component">
                <b>recommender</b> association rules in gold <code>{shop.recommendations}</code>
              </span>
            )}
          </div>
          {depth === "engineer" && (
            <pre className="dp-json">{JSON.stringify({ ml_exports: exports }, null, 2)}</pre>
          )}
        </>
      );
    }
    case "governance":
      return (
        <>
          <h5>{label}</h5>
          <div className="dp-proof-row">
            You are: <b>{overview?.access.role_label ?? "…"}</b>
            {overview?.access.row_filter && <span className="dp-role-pill">{overview.access.row_filter.column} ∈ {overview.access.row_filter.values.map((v) => optionLabel(v, overview.row_filter_options)).join(", ")}</span>}
          </div>
          {Object.entries(dp.access.roles).map(([name, r]) => (
            <div key={name} className={`dp-proof-row${name === filterRole ? " dp-proof-row--hl" : ""}`}>
              <b>{r.label}</b> — {r.rows === "filtered" ? `only its own ${dp.access.row_filter?.column}` : "all rows"}
              {r.hidden.length > 0 && <span className="panel-hint"> · hides {r.hidden.join(", ")}</span>}
            </div>
          ))}
          <p className="panel-hint">
            {overview?.tiers?.governance ? (
              <a href={config.openMetadataUrl} target="_blank" rel="noreferrer">
                Open the catalogue (OpenMetadata) <Icon name="external" size={11} />
              </a>
            ) : (
              "Catalogue: governance tier off (make k3s-data-governance-up)."
            )}
          </p>
        </>
      );
    case "capabilities":
      return (
        <div className="dp-capabilities">
          <table className="dp-table">
            <thead>
              <tr>
                <th>Capability (what)</th>
                <th>Reference (how)</th>
                {depth !== "simple" && <th>Alternatives</th>}
                <th>Here</th>
              </tr>
            </thead>
            <tbody>
              {CAPABILITIES.map((c) => {
                const up = c.tier === "platform" || overview?.tiers?.[c.tier];
                return (
                  <tr key={c.capability}>
                    <td>
                      <b>{c.capability}</b>
                    </td>
                    <td>{c.reference}</td>
                    {depth !== "simple" && <td className="panel-hint">{c.alternatives}</td>}
                    <td>
                      <span className={`dp-dot${up ? " dp-dot--up" : ""}`} /> {c.tier === "platform" ? "platform" : `${c.tier}${up ? "" : " (off)"}`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      );
  }
}

type Edge = [MapNode, MapNode];
