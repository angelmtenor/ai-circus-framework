import { useEffect, useMemo, useState } from "react";
import { predict, type PredictionResult } from "./apiClient";
import { config } from "./config";
import { formatMetric, optionLabel, type InsightsApp, type MetricResult } from "./dataApi";
import type { DataProductProps } from "./DataProductView";
import { Icon } from "./Icon";
import "./insights.css";

const ROW_COLOURS = ["var(--blue)", "var(--teal)", "var(--amber)", "var(--pink)", "var(--green)", "var(--purple)"];

/**
 * A data product's control room (`data_product.apps.insights`): a season heatmap of one governed
 * metric over its whole history (one row per group, one cell per week, each row on its own
 * scale so every group's seasons show) and a forecast board — the latest feature rows of an
 * ml_export, scored live by its model through the prediction service (with its 90% interval).
 */
export function InsightsView({ scenario, client, overview, accessToken }: DataProductProps) {
  const app = scenario.data_product!.apps!.insights!;
  return (
    <div className="tab-panel insights">
      <div className="insights-hero">
        <Icon name="wave" size={22} />
        <div>
          <h2>{app.title}</h2>
          <p>{app.subtitle}</p>
        </div>
      </div>
      {app.heatmap && <SeasonHeatmap app={app} client={client} scenario={scenario} />}
      {app.forecast && (
        <ForecastBoard
          app={app}
          dataset={scenario.data_product!.ml_exports?.find((e) => e.scenario === app.forecast!.model)?.dataset ?? ""}
          client={client}
          overview={overview}
          accessToken={accessToken ?? null}
        />
      )}
    </div>
  );
}

function SeasonHeatmap({ app, client, scenario }: { app: InsightsApp; client: DataProductProps["client"]; scenario: DataProductProps["scenario"] }) {
  const spec = app.heatmap!;
  const dp = scenario.data_product!;
  const metric = dp.metrics.find((m) => m.name === spec.metric)!;
  const timeColumn = dp.datasets.find((d) => d.name === metric.dataset)?.time_column ?? "week";
  const [result, setResult] = useState<MetricResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<{ row: string; t: string; v: number } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    client
      .metric(spec.metric, { dims: [timeColumn, spec.rows], period: "all", limit: 2000, order: "dimension" }, controller.signal)
      .then(setResult)
      .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    return () => controller.abort();
  }, [client, spec, timeColumn]);

  const grid = useMemo(() => {
    if (!result) return null;
    const times = [...new Set(result.rows.map((r) => String(r[timeColumn])))].sort();
    const rows = [...new Set(result.rows.map((r) => String(r[spec.rows])))].sort();
    const value = new Map(result.rows.map((r) => [`${r[spec.rows]}|${r[timeColumn]}`, Number(r.value ?? 0)]));
    const max = new Map(rows.map((row) => [row, Math.max(1, ...times.map((t) => value.get(`${row}|${t}`) ?? 0))]));
    return { times, rows, value, max };
  }, [result, spec.rows, timeColumn]);

  const W = 1100;
  const label = 90;
  const rowH = 34;
  return (
    <section className="panel-card insights-card">
      <div className="dp-panel-head">
        <h4>{spec.title}</h4>
        <span className="panel-hint">each row on its own scale · {metric.label.toLowerCase()} · governed metric <code>{metric.name}</code></span>
      </div>
      {error && <p className="panel-hint dp-restricted">{error}</p>}
      {!grid && !error && <div style={{ height: 180 }} aria-busy="true" />}
      {grid && grid.times.length > 0 && (
        <div className="insights-heat-wrap">
          <svg viewBox={`0 0 ${W} ${grid.rows.length * rowH + 30}`} className="insights-heat" role="img" aria-label={spec.title}>
            {grid.rows.map((row, ri) => {
              const colour = ROW_COLOURS[ri % ROW_COLOURS.length];
              const cw = (W - label) / grid.times.length;
              return (
                <g key={row} transform={`translate(0, ${ri * rowH})`}>
                  <text x={label - 10} y={rowH / 2} className="insights-heat-label">
                    {row}
                  </text>
                  {grid.times.map((t, ti) => {
                    const v = grid.value.get(`${row}|${t}`) ?? 0;
                    const k = v / grid.max.get(row)!;
                    return (
                      <rect
                        key={t}
                        x={label + ti * cw}
                        y={3}
                        width={Math.max(1, cw - 0.6)}
                        height={rowH - 6}
                        rx={1.5}
                        fill={colour}
                        opacity={0.08 + 0.92 * k * k}
                        onMouseEnter={() => setHover({ row, t, v })}
                        onMouseLeave={() => setHover(null)}
                      />
                    );
                  })}
                </g>
              );
            })}
            {grid.times.map((t, ti) =>
              t.slice(5, 10) <= "01-07" && (ti === 0 || grid.times[ti - 1].slice(0, 4) !== t.slice(0, 4)) ? (
                <g key={t}>
                  <line x1={label + (ti * (W - label)) / grid.times.length} x2={label + (ti * (W - label)) / grid.times.length} y1={0} y2={grid.rows.length * rowH} className="insights-year-line" />
                  <text x={label + (ti * (W - label)) / grid.times.length + 4} y={grid.rows.length * rowH + 18} className="insights-year">
                    {t.slice(0, 4)}
                  </text>
                </g>
              ) : null,
            )}
          </svg>
          <div className="insights-heat-tip">
            {hover ? (
              <>
                <b>{hover.row}</b> · week of {hover.t.slice(0, 10)} · {formatMetric(hover.v, metric.format)}
              </>
            ) : (
              <span className="panel-hint">Hover a week. Summer lights up SUP, spring and autumn light up kites, Black Friday flashes every row.</span>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

type Cell = { row: string; column: string; baseline: number; forecast: PredictionResult | null; week: string };

function ForecastBoard({
  app,
  dataset,
  client,
  overview,
  accessToken,
}: {
  app: InsightsApp;
  dataset: string;
  client: DataProductProps["client"];
  overview: DataProductProps["overview"];
  accessToken: string | null;
}) {
  const spec = app.forecast!;
  const [cells, setCells] = useState<Cell[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    // The latest rows of the model's feature mart, then one batch prediction (no SHAP needed here).
    client
      .datasetRows(dataset, 500, controller.signal, { [spec.latest_flag]: "true" })
      .then(async (r) => {
        const rows = r.rows;
        if (rows.length === 0) throw new Error("No rows to forecast yet — the feature mart is built after the next transform.");
        const scored = await predict(config.predictionUrl, spec.model, rows, accessToken, { explain: false });
        if (!alive) return;
        setCells(
          rows.map((row, i) => ({
            row: String(row[spec.row]),
            column: String(row[spec.column]),
            baseline: Number(row[spec.baseline] ?? 0),
            forecast: scored.predictions[i] ?? null,
            week: String(row.week ?? ""),
          })),
        );
      })
      .catch((e: Error) => alive && e.name !== "AbortError" && setError(e.message));
    return () => {
      alive = false;
      controller.abort();
    };
  }, [client, spec, dataset, accessToken]);

  const columns = useMemo(() => [...new Set((cells ?? []).map((c) => c.column))].sort(), [cells]);
  const rows = useMemo(() => [...new Set((cells ?? []).map((c) => c.row))].sort(), [cells]);
  const at = useMemo(() => new Map((cells ?? []).map((c) => [`${c.row}|${c.column}`, c])), [cells]);
  const totals = useMemo(
    () => columns.map((col) => (cells ?? []).filter((c) => c.column === col).reduce((s, c) => s + (c.forecast?.prediction ?? 0), 0)),
    [cells, columns],
  );
  const week = cells?.[0]?.week;

  return (
    <section className="panel-card insights-card">
      <div className="dp-panel-head">
        <h4>{spec.title}</h4>
        <span className="panel-hint">
          model <code>{spec.model}</code> (LightGBM, trained on the gold feature mart){week ? ` · from the week of ${week.slice(0, 10)}` : ""}
        </span>
      </div>
      {error && <p className="panel-hint dp-restricted">{error}</p>}
      {!cells && !error && <div style={{ height: 220 }} aria-busy="true" />}
      {cells && (
        <>
          <div className="insights-totals">
            {columns.map((col, i) => (
              <div key={col} className="kpi-tile">
                <div className="kpi-label">{col}</div>
                <div className="kpi-value">{Math.round(totals[i]).toLocaleString()}</div>
                <div className="kpi-sub">units forecast, all shops</div>
              </div>
            ))}
          </div>
          <div className="insights-board-wrap">
            <table className="insights-board">
              <thead>
                <tr>
                  <th />
                  {columns.map((col) => (
                    <th key={col}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row}>
                    <th>{optionLabel(row, overview?.row_filter_options).replace("Angel's Surf Co. ", "")}</th>
                    {columns.map((col) => {
                      const c = at.get(`${row}|${col}`);
                      if (!c?.forecast) return <td key={col} className="insights-empty">—</td>;
                      const f = c.forecast.prediction;
                      const delta = c.baseline > 0 ? (f - c.baseline) / c.baseline : 0;
                      const tone = delta > 0.1 ? "up" : delta < -0.1 ? "down" : "flat";
                      const band = c.forecast.prediction_lower != null && c.forecast.prediction_upper != null;
                      return (
                        <td key={col} className={`insights-cell insights-cell--${tone}`} title={band ? `90% interval ${c.forecast.prediction_lower!.toFixed(0)}–${c.forecast.prediction_upper!.toFixed(0)} · this week ${c.baseline}` : `this week ${c.baseline}`}>
                          <b>{f.toFixed(0)}</b>
                          <span>{tone === "up" ? "▲" : tone === "down" ? "▼" : "•"} {c.baseline}</span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="panel-hint">
            Each cell: next week's forecast units (bold) and this week's actual units — ▲/▼ when the model expects a change of more than 10%. Hover for the 90% interval. Ask the assistant “why?” for the feature contributions.
          </p>
        </>
      )}
    </section>
  );
}

