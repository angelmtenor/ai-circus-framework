import { useEffect, useMemo, useState } from "react";
import { BarList, CATEGORY_PALETTE } from "./charts";
import { DashBarChart, DashLineChart } from "./dashboardCharts";
import { formatDate, isIsoDate, panelSeries } from "./dashboardChartLogic";
import { changeLabel, formatMetric, type DashboardPanel, type MetricResult, type MetricSpec } from "./dataApi";
import type { DataProductProps } from "./DataProductView";
import { config } from "./config";
import { DataStoreMap } from "./DataStoreMap";
import { Icon } from "./Icon";

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

  const isList = panel.chart === "hbar" || panel.chart === "pie";
  const [now] = useState(() => new Date());
  const view = useMemo(() => (result && !isList ? panelSeries(panel, result, now) : null), [result, panel, isList, now]);
  const formatValue = (value: number | null) => formatMetric(value, spec.format);
  const tipTitle = (category: string) => {
    if (!isIsoDate(category)) return category;
    return panel.dims[0] === "week" ? `Week of ${formatDate(category)}` : formatDate(category);
  };
  const chartProps = view && {
    categories: view.categories,
    series: view.series,
    format: spec.format,
    label: `${panel.title}: ${spec.label} across ${view.categories.length} ${panel.dims[0]} values.`,
    tipTitle,
    formatValue,
  };
  const listTotal = result?.rows.reduce((sum, r) => sum + Math.abs(r.value ?? 0), 0) ?? 0;

  return (
    <div className={`panel-card dp-panel${panel.wide ? " dp-panel--wide" : ""}`}>
      <div className="dp-panel-head">
        <h4>{panel.title}</h4>
        {result && <span className="panel-hint">total {formatMetric(result.total, spec.format)}</span>}
      </div>
      {error && <p className="panel-hint dp-restricted">{error.includes("Role") || error.includes("only sees") ? `Not available for your role — ${error}` : error}</p>}
      {!error && !result && <div style={{ height: 260 }} aria-busy="true" />}
      {result && result.rows.length === 0 && <p className="panel-hint">No data in this period.</p>}
      {result && result.rows.length > 0 && isList && (
        // A part-to-whole panel ("pie") reads as ranked bars with each share written next to it.
        <BarList
          items={result.rows.map((r) => ({ label: String(r[panel.dims[0]]), value: r.value ?? 0 }))}
          signed={false}
          neutralColor={CATEGORY_PALETTE[0]}
          valueFormatter={(v) => (panel.chart === "pie" && listTotal > 0 ? `${formatValue(v)} · ${((100 * v) / listTotal).toFixed(0)}%` : formatValue(v))}
        />
      )}
      {chartProps && view.categories.length > 0 && (panel.chart === "line" ? <DashLineChart {...chartProps} /> : <DashBarChart {...chartProps} />)}
      {view?.inProgress && (
        <p className="panel-hint dp-metric-def">
          {view.inProgress.dim === "day" || view.inProgress.dim === "date"
            ? "Today is"
            : `The ${view.inProgress.dim} of ${formatDate(view.inProgress.bucket)} is`}{" "}
          still in progress, so it is left out.
        </p>
      )}
      <p className="panel-hint dp-metric-def">
        <code>{spec.name}</code> = {spec.expression} over <code>{spec.dataset}</code>
      </p>
    </div>
  );
}
