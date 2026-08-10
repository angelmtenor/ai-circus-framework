/**
 * Pure logic (no JSX, no Plotly import) that turns a chart config + a row sample
 * into Plotly `data`/`layout` — the engine behind the Data tab's dashboard (see
 * DataView.tsx). Kept separate from PlotlyChart.tsx so it's trivially unit-testable
 * and has zero runtime dependency on plotly.js itself.
 */
import type { ChartAgg, ChartSpec, ChartType } from "./apiClient";
import type { DatasetRow } from "./DatasetFilterPanel";
import type { PlotlyDatum, PlotlyLayout } from "./plotly";

// The live-editable analog of the YAML ChartSpec — "" means unset (plays nicer with
// controlled <select> elements than undefined/null does).
export type ChartCardConfig = {
  id: string;
  type: ChartType;
  x: string;
  y: string;
  z: string;
  colorBy: string;
  agg: ChartAgg;
};

let nextId = 0;
function newId(): string {
  nextId += 1;
  return `chart-${nextId}`;
}

export function specToChartCardConfig(spec: ChartSpec): ChartCardConfig {
  return {
    id: newId(),
    type: spec.type,
    x: spec.x ?? "",
    y: spec.y ?? "",
    z: spec.z ?? "",
    colorBy: spec.color_by ?? "",
    agg: spec.agg ?? "count",
  };
}

export function defaultChartCardConfig(numericFeatures: string[], categoricalFeatures: string[]): ChartCardConfig {
  if (numericFeatures.length > 0) {
    return { id: newId(), type: "histogram", x: numericFeatures[0], y: "", z: "", colorBy: "", agg: "count" };
  }
  return { id: newId(), type: "bar", x: categoricalFeatures[0] ?? "", y: "", z: "", colorBy: "", agg: "count" };
}

function toNumber(v: unknown): number {
  return Number(v);
}

function withFiniteNumber(rows: DatasetRow[], col: string): DatasetRow[] {
  return rows.filter((r) => Number.isFinite(toNumber(r[col])));
}

function aggregate(values: number[], agg: ChartAgg): number {
  if (values.length === 0) return 0;
  switch (agg) {
    case "sum":
      return values.reduce((a, b) => a + b, 0);
    case "mean":
      return values.reduce((a, b) => a + b, 0) / values.length;
    case "min":
      return Math.min(...values);
    case "max":
      return Math.max(...values);
    case "count":
    default:
      return values.length;
  }
}

/** Splits rows into one group per distinct `colorBy` value (insertion order), or a
 * single unnamed group when `colorBy` is unset. */
function splitByColor(rows: DatasetRow[], colorBy: string): { key: string; rows: DatasetRow[] }[] {
  if (!colorBy) return [{ key: "", rows }];
  const groups = new Map<string, DatasetRow[]>();
  for (const r of rows) {
    const key = String(r[colorBy]);
    const bucket = groups.get(key);
    if (bucket) bucket.push(r);
    else groups.set(key, [r]);
  }
  return [...groups.entries()].map(([key, groupRows]) => ({ key, rows: groupRows }));
}

/** Every column whose value is a finite number (or null) across the whole sample —
 * used by heatmap's no-args correlation-matrix default. */
function inferNumericColumns(rows: DatasetRow[]): string[] {
  if (rows.length === 0) return [];
  return Object.keys(rows[0]).filter((k) => rows.every((r) => r[k] === null || Number.isFinite(toNumber(r[k]))));
}

function correlation(a: number[], b: number[]): number {
  const n = a.length;
  if (n === 0) return 0;
  const meanA = a.reduce((s, v) => s + v, 0) / n;
  const meanB = b.reduce((s, v) => s + v, 0) / n;
  let cov = 0;
  let varA = 0;
  let varB = 0;
  for (let i = 0; i < n; i++) {
    const da = a[i] - meanA;
    const db = b[i] - meanB;
    cov += da * db;
    varA += da * da;
    varB += db * db;
  }
  const denom = Math.sqrt(varA * varB);
  return denom === 0 ? 0 : cov / denom;
}

function buildHistogram(rows: DatasetRow[], cfg: ChartCardConfig, palette: string[]): { data: PlotlyDatum[]; layout: PlotlyLayout } {
  if (!cfg.x) return { data: [], layout: {} };
  const groups = splitByColor(rows, cfg.colorBy);
  const data = groups.map((g, i) => ({
    type: "histogram",
    x: withFiniteNumber(g.rows, cfg.x).map((r) => toNumber(r[cfg.x])),
    name: g.key || cfg.x,
    marker: { color: palette[i % palette.length] },
    opacity: groups.length > 1 ? 0.7 : 1,
  }));
  return {
    data,
    layout: { barmode: "overlay", xaxis: { title: cfg.x }, yaxis: { title: "count" }, showlegend: groups.length > 1 },
  };
}

function buildBox(rows: DatasetRow[], cfg: ChartCardConfig, palette: string[]): { data: PlotlyDatum[]; layout: PlotlyLayout } {
  if (!cfg.y) return { data: [], layout: {} };
  const filtered = withFiniteNumber(rows, cfg.y);
  if (!cfg.x) {
    return { data: [{ type: "box", y: filtered.map((r) => toNumber(r[cfg.y])), marker: { color: palette[0] } }], layout: { yaxis: { title: cfg.y } } };
  }
  return {
    data: [
      {
        type: "box",
        x: filtered.map((r) => String(r[cfg.x])),
        y: filtered.map((r) => toNumber(r[cfg.y])),
        marker: { color: palette[0] },
      },
    ],
    layout: { xaxis: { title: cfg.x }, yaxis: { title: cfg.y } },
  };
}

/** A `colorBy` column is treated as a continuous gradient (not a discrete group per
 * distinct value) when every value in the plotted rows is numeric — matches how
 * numericFeatures/categoricalFeatures are split for the feature-schema-driven
 * dropdowns elsewhere in DataView.tsx. */
function isNumericColumn(rows: DatasetRow[], col: string): boolean {
  return rows.length > 0 && rows.every((r) => Number.isFinite(toNumber(r[col])));
}

function buildScatter(rows: DatasetRow[], cfg: ChartCardConfig, palette: string[], is3d: boolean): { data: PlotlyDatum[]; layout: PlotlyLayout } {
  if (!cfg.x || !cfg.y || (is3d && !cfg.z)) return { data: [], layout: {} };
  let candidateRows = withFiniteNumber(withFiniteNumber(rows, cfg.x), cfg.y);
  if (is3d) candidateRows = withFiniteNumber(candidateRows, cfg.z);

  const sceneOrAxes: PlotlyLayout = is3d
    ? { scene: { xaxis: { title: cfg.x }, yaxis: { title: cfg.y }, zaxis: { title: cfg.z } } }
    : { xaxis: { title: cfg.x }, yaxis: { title: cfg.y } };

  // Continuous gradient: one trace, marker.color driven by a Plotly colorscale
  // (with its own colorbar) rather than one trace per distinct value — a discrete
  // per-value split would be meaningless (and enormous) for a continuous column.
  if (cfg.colorBy && isNumericColumn(candidateRows, cfg.colorBy)) {
    const trace: PlotlyDatum = {
      type: is3d ? "scatter3d" : "scattergl",
      mode: "markers",
      x: candidateRows.map((r) => toNumber(r[cfg.x])),
      y: candidateRows.map((r) => toNumber(r[cfg.y])),
      name: "points",
      marker: {
        size: is3d ? 4 : 6,
        opacity: 0.85,
        color: candidateRows.map((r) => toNumber(r[cfg.colorBy])),
        colorscale: "Viridis",
        showscale: true,
        colorbar: { title: { text: cfg.colorBy } },
      },
    };
    if (is3d) trace.z = candidateRows.map((r) => toNumber(r[cfg.z]));
    return { data: [trace], layout: sceneOrAxes };
  }

  const groups = splitByColor(candidateRows, cfg.colorBy);
  const data = groups.map((g, i) => {
    const trace: PlotlyDatum = {
      type: is3d ? "scatter3d" : "scattergl",
      mode: "markers",
      x: g.rows.map((r) => toNumber(r[cfg.x])),
      y: g.rows.map((r) => toNumber(r[cfg.y])),
      name: g.key || "points",
      marker: { size: is3d ? 4 : 6, color: palette[i % palette.length], opacity: 0.8 },
    };
    if (is3d) trace.z = g.rows.map((r) => toNumber(r[cfg.z]));
    return trace;
  });
  return { data, layout: { ...sceneOrAxes, showlegend: groups.length > 1 } };
}

function buildLine(rows: DatasetRow[], cfg: ChartCardConfig, palette: string[]): { data: PlotlyDatum[]; layout: PlotlyLayout } {
  if (!cfg.x || !cfg.y) return { data: [], layout: {} };
  const groups = splitByColor(rows, cfg.colorBy);
  const data = groups.map((g, i) => {
    const filtered = withFiniteNumber(withFiniteNumber(g.rows, cfg.x), cfg.y).sort((a, b) => toNumber(a[cfg.x]) - toNumber(b[cfg.x]));
    return {
      type: "scatter",
      mode: "lines+markers",
      x: filtered.map((r) => toNumber(r[cfg.x])),
      y: filtered.map((r) => toNumber(r[cfg.y])),
      name: g.key || cfg.y,
      line: { color: palette[i % palette.length] },
      marker: { color: palette[i % palette.length] },
    };
  });
  return { data, layout: { xaxis: { title: cfg.x }, yaxis: { title: cfg.y }, showlegend: groups.length > 1 } };
}

function bucketValues(rows: DatasetRow[], x: string, y: string): Map<string, number[]> {
  const buckets = new Map<string, number[]>();
  for (const r of rows) {
    const key = String(r[x]);
    const bucket = buckets.get(key) ?? [];
    if (!buckets.has(key)) buckets.set(key, bucket);
    if (y) {
      const v = toNumber(r[y]);
      if (Number.isFinite(v)) bucket.push(v);
    } else {
      bucket.push(1);
    }
  }
  return buckets;
}

function buildBar(rows: DatasetRow[], cfg: ChartCardConfig, palette: string[]): { data: PlotlyDatum[]; layout: PlotlyLayout } {
  if (!cfg.x) return { data: [], layout: {} };
  const groups = splitByColor(rows, cfg.colorBy);
  const data = groups.map((g, i) => {
    const buckets = bucketValues(g.rows, cfg.x, cfg.y);
    const categories = [...buckets.keys()];
    const values = categories.map((c) => (cfg.y ? aggregate(buckets.get(c)!, cfg.agg) : buckets.get(c)!.length));
    return { type: "bar", x: categories, y: values, name: g.key || cfg.x, marker: { color: palette[i % palette.length] } };
  });
  return {
    data,
    layout: {
      barmode: groups.length > 1 ? "group" : "stack",
      xaxis: { title: cfg.x },
      yaxis: { title: cfg.y ? `${cfg.agg}(${cfg.y})` : "count" },
      showlegend: groups.length > 1,
    },
  };
}

function buildPie(rows: DatasetRow[], cfg: ChartCardConfig, palette: string[]): { data: PlotlyDatum[]; layout: PlotlyLayout } {
  if (!cfg.x) return { data: [], layout: {} };
  const buckets = bucketValues(rows, cfg.x, cfg.y);
  const labels = [...buckets.keys()];
  const values = labels.map((l) => (cfg.y ? aggregate(buckets.get(l)!, cfg.agg) : buckets.get(l)!.length));
  return {
    data: [{ type: "pie", labels, values, hole: 0.35, marker: { colors: labels.map((_, i) => palette[i % palette.length]) } }],
    layout: {},
  };
}

function buildHeatmap(rows: DatasetRow[], cfg: ChartCardConfig): { data: PlotlyDatum[]; layout: PlotlyLayout } {
  if (cfg.x && cfg.y) {
    const filtered = withFiniteNumber(withFiniteNumber(rows, cfg.x), cfg.y);
    return {
      data: [{ type: "histogram2d", x: filtered.map((r) => toNumber(r[cfg.x])), y: filtered.map((r) => toNumber(r[cfg.y])), colorscale: "Blues" }],
      layout: { xaxis: { title: cfg.x }, yaxis: { title: cfg.y } },
    };
  }
  // No axes chosen: a correlation matrix across every numeric column is a more
  // useful default than an empty chart.
  const numericColumns = inferNumericColumns(rows);
  const columnValues = numericColumns.map((c) => rows.map((r) => toNumber(r[c])));
  const z = columnValues.map((a) => columnValues.map((b) => correlation(a, b)));
  return {
    data: [{ type: "heatmap", x: numericColumns, y: numericColumns, z, zmin: -1, zmax: 1, colorscale: "RdBu" }],
    layout: {},
  };
}

export function buildChart(rows: DatasetRow[], cfg: ChartCardConfig, palette: string[]): { data: PlotlyDatum[]; layout: PlotlyLayout } {
  switch (cfg.type) {
    case "histogram":
      return buildHistogram(rows, cfg, palette);
    case "box":
      return buildBox(rows, cfg, palette);
    case "scatter":
      return buildScatter(rows, cfg, palette, false);
    case "scatter3d":
      return buildScatter(rows, cfg, palette, true);
    case "line":
      return buildLine(rows, cfg, palette);
    case "bar":
      return buildBar(rows, cfg, palette);
    case "pie":
      return buildPie(rows, cfg, palette);
    case "heatmap":
      return buildHeatmap(rows, cfg);
    default:
      return { data: [], layout: {} };
  }
}
