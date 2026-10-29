import { useEffect, useMemo, useState } from "react";
import { changeLabel, formatMetric, type DashboardPanel, type MetricResult, type MetricSpec } from "./dataApi";
import type { DataProductProps } from "./DataProductView";
import { config } from "./config";
import { DataStoreMap } from "./DataStoreMap";
import { Icon } from "./Icon";
import { PlotlyChart } from "./PlotlyChart";
import type { PlotlyDatum } from "./plotly";

const PERIODS: { key: string; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "last_7_days", label: "Last 7 days" },
  { key: "last_30_days", label: "Last 30 days" },
  { key: "last_90_days", label: "Last 90 days" },
];
const COMPARES: { key: string; label: string }[] = [
  { key: "same_period_last_week", label: "vs same days last week" },
  { key: "previous_period", label: "vs previous period" },
];

/** Plot-ready value: money in euros, everything else as is. */
function plotValue(value: number | null, format: MetricSpec["format"]): number | null {
  if (value === null) return null;
  return format === "currency_cents" ? value / 100 : value;
}

/**
 * Native, governed dashboards of a data product: the `kpis` and `dashboard` panels the
 * scenario declares, each one a metric from data-api (same definitions the assistant and BI
 * use, same tenant scope and row filter). A panel whose dataset the current role can't read
 * says so instead of failing — switch "view as" to see it happen. Superset, when its tier is
 * up, is one click away for free-form BI.
 */
export function DataDashboardView({ scenario, client, overview }: DataProductProps) {
  const dp = scenario.data_product!;
  const [period, setPeriod] = useState("last_7_days");
  const [compare, setCompare] = useState("same_period_last_week");
  const metrics = useMemo(() => new Map(dp.metrics.map((m) => [m.name, m])), [dp.metrics]);
  const [kpis, setKpis] = useState<Record<string, MetricResult | string>>({});

  useEffect(() => {
    const controller = new AbortController();
    setKpis({});
    for (const name of dp.kpis) {
      client
        .metric(name, { period, compare }, controller.signal)
        .then((r) => setKpis((k) => ({ ...k, [name]: r })))
        .catch((e: Error) => e.name !== "AbortError" && setKpis((k) => ({ ...k, [name]: e.message })));
    }
    return () => controller.abort();
  }, [client, dp.kpis, period, compare]);

  return (
    <div className="tab-panel">
      <div className="panel-card dp-dash-controls">
        <div className="dp-segmented">
          {PERIODS.map((p) => (
            <button key={p.key} className={period === p.key ? "active" : ""} onClick={() => setPeriod(p.key)}>
              {p.label}
            </button>
          ))}
        </div>
        <select value={compare} onChange={(e) => setCompare(e.target.value)}>
          {COMPARES.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
        {overview?.tiers?.bi && (
          <a className="dp-tier-link" href={config.supersetUrl} target="_blank" rel="noreferrer">
            Open in Superset <Icon name="external" size={12} />
          </a>
        )}
        <span className="panel-hint">
          Curated data{overview?.transform?.curated_through ? ` through ${new Date(overview.transform.curated_through).toLocaleTimeString()}` : ""} · marts on{" "}
          {overview?.marts_engine}
        </span>
      </div>
      <div className="dp-kpis">
        {dp.kpis.map((name) => {
          const spec = metrics.get(name);
          const result = kpis[name];
          if (!spec) return null;
          if (typeof result === "string") {
            return (
              <div className="kpi-tile dp-kpi--restricted" key={name} title={result}>
                <div className="kpi-label">{spec.label}</div>
                <div className="kpi-value dp-small">not available</div>
                <div className="kpi-sub">for your role</div>
              </div>
            );
          }
          const change = changeLabel(result?.compare?.change_pct);
          return (
            <div className="kpi-tile" key={name} title={spec.description}>
              <div className="kpi-label">{spec.label}</div>
              <div className="kpi-value">{result ? formatMetric(result.total, spec.format) : "…"}</div>
              <div className={`kpi-sub dp-change dp-change--${change.tone}`}>
                {result?.compare ? `${change.text} (${formatMetric(result.compare.total, spec.format)})` : " "}
              </div>
            </div>
          );
        })}
      </div>
      <div className="dp-panels">
        {dp.dashboard.map((panel) =>
          panel.chart === "map" && panel.places ? (
            <DataStoreMap key={panel.title} panel={panel} specs={metrics} client={client} period={panel.period ?? period} compare={compare} />
          ) : (
            <Panel key={panel.title} panel={panel} spec={metrics.get(panel.metric)!} client={client} period={panel.period ?? period} compare={compare} />
          ),
        )}
      </div>
    </div>
  );
}

function Panel({
  panel,
  spec,
  client,
  period,
  compare,
}: {
  panel: DashboardPanel;
  spec: MetricSpec;
  client: DataProductProps["client"];
  period: string;
  compare: string;
}) {
  const [result, setResult] = useState<MetricResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setError(null);
    const timeSeries = panel.chart === "line";
    client
      .metric(
        panel.metric,
        {
          dims: panel.dims,
          period,
          compare: panel.compare ? compare : undefined,
          limit: timeSeries ? 2000 : panel.limit,
          order: timeSeries || panel.dims[0]?.includes("hour") ? "dimension" : "value_desc",
        },
        controller.signal,
      )
      .then(setResult)
      .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    return () => controller.abort();
  }, [client, panel, period, compare]);

  const data = useMemo<PlotlyDatum[]>(() => {
    if (!result) return [];
    const [x, split] = panel.dims;
    const val = (v: number | null | undefined) => plotValue(v ?? null, spec.format);
    if (panel.chart === "pie") {
      return [{ type: "pie", labels: result.rows.map((r) => String(r[x])), values: result.rows.map((r) => val(r.value)), hole: 0.45, sort: false }];
    }
    if (panel.chart === "hbar") {
      const rows = [...result.rows].reverse();
      return [{ type: "bar", orientation: "h", y: rows.map((r) => String(r[x])), x: rows.map((r) => val(r.value)), name: spec.label }];
    }
    if (split) {
      const groups = new Map<string, typeof result.rows>();
      for (const row of result.rows) {
        const key = String(row[split]);
        groups.set(key, [...(groups.get(key) ?? []), row]);
      }
      return [...groups.entries()].map(([name, rows]) => ({
        type: panel.chart === "line" ? "scatter" : "bar",
        mode: "lines+markers",
        name,
        x: rows.map((r) => r[x]),
        y: rows.map((r) => val(r.value)),
      }));
    }
    const traces: PlotlyDatum[] = [
      { type: panel.chart === "line" ? "scatter" : "bar", name: result.period.from === result.period.to ? result.period.from : "this period", x: result.rows.map((r) => String(r[x])), y: result.rows.map((r) => val(r.value)) },
    ];
    if (panel.compare && result.compare) {
      traces.push({ type: "bar", name: "comparison", x: result.rows.map((r) => String(r[x])), y: result.rows.map((r) => val(r.compare_value)), opacity: 0.45 });
    }
    return traces;
  }, [result, panel, spec]);

  const unit = spec.format === "currency_cents" ? "€" : spec.format === "percent" ? "%" : "";
  return (
    <div className={`panel-card dp-panel${panel.wide ? " dp-panel--wide" : ""}`}>
      <div className="dp-panel-head">
        <h4>{panel.title}</h4>
        {result && <span className="panel-hint">total {formatMetric(result.total, spec.format)}</span>}
      </div>
      {error && <p className="panel-hint dp-restricted">{error.includes("Role") || error.includes("only sees") ? `Not available for your role — ${error}` : error}</p>}
      {!error && !result && <div style={{ height: 260 }} aria-busy="true" />}
      {result && result.rows.length === 0 && <p className="panel-hint">No data in this period.</p>}
      {result && result.rows.length > 0 && (
        <PlotlyChart
          data={data}
          height={panel.chart === "hbar" ? Math.max(260, result.rows.length * 26) : 280}
          layout={{
            barmode: "group",
            margin: { l: panel.chart === "hbar" ? 180 : 48, r: 12, t: 8, b: 48 },
            showlegend: data.length > 1 || panel.chart === "pie",
            legend: { orientation: "h", y: -0.2 },
            yaxis: panel.chart === "hbar" ? { automargin: true } : { title: { text: unit }, rangemode: "tozero" },
            xaxis: panel.chart === "hbar" ? { title: { text: unit } } : { automargin: true },
          }}
        />
      )}
      <p className="panel-hint dp-metric-def">
        <code>{spec.name}</code> = {spec.expression} over <code>{spec.dataset}</code>
      </p>
    </div>
  );
}
