import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent, type RefObject } from "react";
import { getPlatformResources, type GpuDevice, type ResourceReport, type WorkloadUsage } from "./apiClient";

/**
 * Admin Platform → **Monitor** tab: live CPU / memory / GPU usage of the cluster and of
 * every workload, from data-platform-manager's GET /platform/resources (see its
 * core/resources.py): the Kubernetes Metrics API for CPU/memory, NVML for the GPU, and the
 * host's own memory + swap. Memory leads, answering "is the cluster about to run out?": the
 * k3s node's usage, the host's remaining headroom (what the kernel's OOM killer actually
 * looks at — other host processes eat the same RAM) and the kubelet's MemoryPressure. The
 * server keeps no history — the sparklines are this tab's own samples, kept only while it
 * is open (a few minutes), which is all a "what is eating my laptop right now" view needs.
 *
 * Forms: a KPI row of stat tiles (value + meter against capacity + sparkline trend), then
 * a table for the per-workload breakdown (too many rows for a chart; each row's memory
 * meter is against that pod's own limit — the number that predicts an OOM kill).
 */

const POLL_SECONDS = 5;
const HISTORY_POINTS = 72; // 6 minutes at 5 s
const GIB = 1024 ** 3;
const MIB = 1024 ** 2;

type Sample = {
  t: number;
  cpu: number | null;
  memory: number | null;
  hostMemory: number | null;
  gpu: number | null;
  gpuMemory: number | null;
};

type Severity = "ok" | "warn" | "crit";

function severity(pct: number, warnAt = 70, critAt = 90): Severity {
  if (pct >= critAt) return "crit";
  if (pct >= warnAt) return "warn";
  return "ok";
}

const SEVERITY_LABEL: Record<Severity, string | null> = { ok: null, warn: "high", crit: "critical" };
// The host's used share: past these the kernel starts swapping hard, then OOM-killing pods.
const HOST_WARN_PCT = 80;
const HOST_CRIT_PCT = 90;

function formatBytes(bytes: number): string {
  if (bytes >= GIB) return `${(bytes / GIB).toFixed(1)} GiB`;
  return `${Math.round(bytes / MIB)} MiB`;
}

function formatCpu(cores: number): string {
  if (cores >= 1) return `${cores.toFixed(2)} cores`;
  return `${Math.round(cores * 1000)}m`;
}

function pct(used: number | null, total: number | null): number | null {
  if (used === null || total === null || total <= 0) return null;
  return Math.min(100, (used / total) * 100);
}

function sampleOf(report: ResourceReport): Sample {
  const cpuUsed = report.nodes.reduce((s, n) => s + n.cpu_cores, 0);
  const cpuTotal = report.nodes.reduce((s, n) => s + n.cpu_allocatable_cores, 0);
  const memUsed = report.nodes.reduce((s, n) => s + n.memory_bytes, 0);
  const memTotal = report.nodes.reduce((s, n) => s + n.memory_allocatable_bytes, 0);
  const gpu = report.gpu.devices[0];
  return {
    t: Date.parse(report.checked_at),
    cpu: report.available ? pct(cpuUsed, cpuTotal) : null,
    memory: report.available ? pct(memUsed, memTotal) : null,
    hostMemory: report.host ? pct(report.host.total_bytes - report.host.available_bytes, report.host.total_bytes) : null,
    gpu: gpu?.utilization_pct ?? null,
    gpuMemory: gpu ? pct(gpu.memory_used_bytes, gpu.memory_total_bytes) : null,
  };
}

/** Same-ramp meter: the fill carries severity; the track is a light step of the same hue. */
function Meter({ value, label, warnAt, critAt }: { value: number; label: string; warnAt?: number; critAt?: number }) {
  const sev = severity(value, warnAt, critAt);
  return (
    <div
      className={`monitor-meter monitor-meter--${sev}`}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value)}
      aria-label={label}
    >
      <div className="monitor-meter-fill" style={{ width: `${Math.max(value, 0.5)}%` }} />
    </div>
  );
}

/** Width of an element, tracked — the sparkline draws in real pixels so its dot stays round. */
function useWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

const SPARK_HEIGHT = 40;
const SPARK_PAD = 5; // room for the 4px end-dot + its ring

/** One series on a fixed 0–100 % scale (every tile is a share of a capacity), with a hover readout. */
function Sparkline({ samples, pick, label }: { samples: Sample[]; pick: (s: Sample) => number | null; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const points = samples.map((s, i) => ({ i, t: s.t, v: pick(s) })).filter((p) => p.v !== null) as {
    i: number;
    t: number;
    v: number;
  }[];
  const innerW = Math.max(width - SPARK_PAD * 2, 1);
  const x = (i: number) => SPARK_PAD + (HISTORY_POINTS <= 1 ? 0 : (i / (HISTORY_POINTS - 1)) * innerW);
  const y = (v: number) => SPARK_PAD + (1 - v / 100) * (SPARK_HEIGHT - SPARK_PAD * 2);
  // Right-align: the newest sample always sits at the right edge, history grows leftwards.
  const offset = HISTORY_POINTS - samples.length;
  const coords = points.map((p) => ({ ...p, x: x(p.i + offset), y: y(p.v) }));
  const line = coords.map((c, k) => `${k === 0 ? "M" : "L"}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join("");
  const baseline = SPARK_HEIGHT - SPARK_PAD;
  // The wash only once the series spans some width — a few seconds of history would
  // otherwise render it as a thin vertical sliver at the right edge.
  const span = coords.length > 1 ? coords[coords.length - 1].x - coords[0].x : 0;
  const area =
    span >= 12 ? `${line}L${coords[coords.length - 1].x.toFixed(1)},${baseline}L${coords[0].x.toFixed(1)},${baseline}Z` : "";
  const last = coords[coords.length - 1];
  const hovered = hover !== null ? coords[hover] : null;

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    if (coords.length === 0) return;
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
    let best = 0;
    for (let k = 1; k < coords.length; k++) if (Math.abs(coords[k].x - px) < Math.abs(coords[best].x - px)) best = k;
    setHover(best);
  };

  return (
    <div className="monitor-spark" ref={ref}>
      <div className="monitor-spark-readout" aria-live="off">
        {hovered
          ? `${hovered.v.toFixed(0)}% at ${new Date(hovered.t).toLocaleTimeString()}`
          : `last ${Math.round((samples.length * POLL_SECONDS) / 60) || "<1"} min`}
      </div>
      {width > 0 && (
        <svg
          width={width}
          height={SPARK_HEIGHT}
          role="img"
          aria-label={`${label}, last ${samples.length} samples`}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        >
          <line className="monitor-spark-grid" x1={SPARK_PAD} x2={width - SPARK_PAD} y1={baseline} y2={baseline} />
          {area && <path className="monitor-spark-area" d={area} />}
          {coords.length > 1 && <path className="monitor-spark-line" d={line} />}
          {hovered && <line className="monitor-spark-cross" x1={hovered.x} x2={hovered.x} y1={SPARK_PAD} y2={baseline} />}
          {(hovered ?? last) && <circle className="monitor-spark-dot" cx={(hovered ?? last).x} cy={(hovered ?? last).y} r={4} />}
        </svg>
      )}
    </div>
  );
}

function StatTile({
  label,
  value,
  display,
  sub,
  meter,
  samples,
  pick,
  footer,
  unavailable,
  warnAt,
  critAt,
  labels = SEVERITY_LABEL,
  alert,
}: {
  label: string;
  /** A 0–100 share: drives the meter, the severity and the sparkline. */
  value: number | null;
  /** Headline text when it isn't the share itself (e.g. "2.9 GiB free"). */
  display?: string;
  sub?: string;
  meter?: boolean;
  samples: Sample[];
  pick: (s: Sample) => number | null;
  footer?: string;
  unavailable?: string | null;
  warnAt?: number;
  critAt?: number;
  labels?: Record<Severity, string | null>;
  /** An always-critical condition that outranks the share (e.g. the kubelet's MemoryPressure). */
  alert?: string | null;
}) {
  const sev = value !== null ? severity(value, warnAt, critAt) : "ok";
  const badge = alert ?? labels[sev];
  return (
    <div className="panel-card monitor-tile">
      <div className="monitor-tile-label">{label}</div>
      {unavailable ? (
        <p className="settings-card-model monitor-tile-unavailable">{unavailable}</p>
      ) : (
        <>
          <div className="monitor-tile-value">
            {display ?? (value === null ? "—" : `${value.toFixed(0)}%`)}
            {badge && (
              <span className={`settings-badge ${alert || sev === "crit" ? "settings-badge--fail" : "settings-badge--partial"}`}>
                {badge}
              </span>
            )}
          </div>
          {sub && <div className="settings-card-model">{sub}</div>}
          {meter && value !== null && <Meter value={value} label={label} warnAt={warnAt} critAt={critAt} />}
          <Sparkline samples={samples} pick={pick} label={label} />
          {footer && <div className="settings-card-model monitor-tile-footer">{footer}</div>}
        </>
      )}
    </div>
  );
}

function gpuFooter(gpu: GpuDevice): string {
  const parts = [];
  if (gpu.temperature_c !== null) parts.push(`${gpu.temperature_c} °C`);
  if (gpu.power_w !== null) parts.push(gpu.power_limit_w !== null ? `${gpu.power_w} / ${gpu.power_limit_w} W` : `${gpu.power_w} W`);
  return parts.join(" · ");
}

function WorkloadRow({ w }: { w: WorkloadUsage }) {
  const ofLimit = pct(w.memory_bytes, w.memory_limit_bytes);
  // A pod past ~85 % of its memory limit is the one the kernel OOM-kills next.
  const sev = ofLimit !== null ? severity(ofLimit, 70, 85) : "ok";
  return (
    <tr>
      <td>
        {w.name}
        {w.pods > 1 && <span className="settings-card-model"> ×{w.pods}</span>}
      </td>
      <td className="monitor-num">{formatCpu(w.cpu_cores)}</td>
      <td className="monitor-num">{formatBytes(w.memory_bytes)}</td>
      <td className="monitor-limit-cell">
        {ofLimit === null ? (
          <span className="settings-card-model">no limit</span>
        ) : (
          <>
            <Meter value={ofLimit} label={`${w.name} memory of limit`} warnAt={70} critAt={85} />
            <span className="monitor-num">
              {ofLimit.toFixed(0)}% of {formatBytes(w.memory_limit_bytes ?? 0)}
            </span>
            {sev === "crit" && <span className="settings-badge settings-badge--fail">near limit</span>}
          </>
        )}
      </td>
    </tr>
  );
}

export function PlatformMonitorTab({ baseUrl, accessToken }: { baseUrl: string; accessToken: string | null }) {
  const [report, setReport] = useState<ResourceReport | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getPlatformResources(baseUrl, accessToken)
      .then((r) => {
        setReport(r);
        setError(null);
        setSamples((prev) => [...prev, sampleOf(r)].slice(-HISTORY_POINTS));
      })
      .catch((e) => setError((e as Error).message));
  }, [baseUrl, accessToken]);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_SECONDS * 1000);
    return () => clearInterval(id);
  }, [load]);

  if (!report) {
    return error ? <p className="error">{error}</p> : <div className="app-loading">Reading cluster metrics…</div>;
  }

  const cpuUsed = report.nodes.reduce((s, n) => s + n.cpu_cores, 0);
  const cpuTotal = report.nodes.reduce((s, n) => s + n.cpu_allocatable_cores, 0);
  const memUsed = report.nodes.reduce((s, n) => s + n.memory_bytes, 0);
  const memTotal = report.nodes.reduce((s, n) => s + n.memory_allocatable_bytes, 0);
  const podMemory = report.workloads.reduce((s, w) => s + w.memory_bytes, 0);
  const current = samples[samples.length - 1] ?? sampleOf(report);
  const gpu = report.gpu.devices[0] ?? null;
  const metricsReason = report.available ? null : report.reason;
  const host = report.host;
  const pressure = report.nodes.some((n) => n.memory_pressure);

  return (
    <>
      <p className="panel-hint">
        Live usage, refreshed every {POLL_SECONDS}s (CPU/memory come from metrics-server's ~15 s windows; GPU is read directly
        through NVML). <strong>Host memory</strong> is the real headroom — it runs out before k3s's own share does when other
        things on the machine use RAM too. Short of memory? Stop an optional service from the <strong>Health</strong> tab.
        {` Last read: ${new Date(report.checked_at).toLocaleTimeString()}.`}
      </p>
      {error && <p className="error">{error}</p>}

      <div className="monitor-tiles">
        <StatTile
          label="Memory — k3s"
          value={current.memory}
          sub={report.available ? `${formatBytes(memUsed)} of ${formatBytes(memTotal)} used by the cluster` : undefined}
          meter
          samples={samples}
          pick={(s) => s.memory}
          unavailable={metricsReason}
          alert={pressure ? "memory pressure — evicting" : null}
        />
        <StatTile
          label="Host memory"
          value={current.hostMemory}
          display={host ? `${formatBytes(host.available_bytes)} free` : undefined}
          sub={host ? `of ${formatBytes(host.total_bytes)} on the machine running k3s` : undefined}
          meter
          samples={samples}
          pick={(s) => s.hostMemory}
          warnAt={HOST_WARN_PCT}
          critAt={HOST_CRIT_PCT}
          labels={{ ok: null, warn: "low", crit: "running out" }}
          footer={
            host && host.swap_total_bytes > 0
              ? `swap: ${formatBytes(host.swap_used_bytes)} of ${formatBytes(host.swap_total_bytes)} in use`
              : undefined
          }
          unavailable={host ? null : "Host memory unreadable from this pod."}
        />
        <StatTile
          label="CPU"
          value={current.cpu}
          sub={report.available ? `${cpuUsed.toFixed(1)} of ${cpuTotal} cores` : undefined}
          meter
          samples={samples}
          pick={(s) => s.cpu}
          unavailable={metricsReason}
        />
        <StatTile
          label="GPU"
          value={current.gpu}
          sub={gpu ? gpu.name : undefined}
          meter
          samples={samples}
          pick={(s) => s.gpu}
          footer={gpu ? gpuFooter(gpu) : undefined}
          unavailable={report.gpu.available ? null : report.gpu.reason}
        />
        {gpu && (
          <StatTile
            label="GPU memory"
            value={current.gpuMemory}
            sub={
              gpu.memory_used_bytes !== null && gpu.memory_total_bytes !== null
                ? `${formatBytes(gpu.memory_used_bytes)} of ${formatBytes(gpu.memory_total_bytes)}`
                : undefined
            }
            meter
            samples={samples}
            pick={(s) => s.gpuMemory}
            footer={report.gpu.driver_version ? `driver ${report.gpu.driver_version}` : undefined}
          />
        )}
      </div>

      {report.available && (
        <section className="platform-group">
          <h3>Workloads</h3>
          <p className="panel-hint">
            Every running pod in the platform namespace, heaviest first — {formatBytes(podMemory)} of the node's {formatBytes(memUsed)}{" "}
            in use (the rest is k3s itself and system pods). The meter is each workload's memory against its own limit: past ~85 % it is
            the next one the kernel OOM-kills.
          </p>
          <div className="monitor-table-wrap">
            <table className="data-table monitor-table">
              <thead>
                <tr>
                  <th>Workload</th>
                  <th title="millicores — 1000m = 1 core">CPU</th>
                  <th>Memory</th>
                  <th>Of its limit</th>
                </tr>
              </thead>
              <tbody>
                {report.workloads.map((w) => (
                  <WorkloadRow key={w.name} w={w} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
