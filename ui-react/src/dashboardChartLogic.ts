/**
 * The pure part of the dashboards' charts: axis scales, tick text, and turning a metric's rows
 * into aligned series. No drawing here; dashboardCharts.tsx does that.
 */
import { CATEGORY_PALETTE, colorScale } from "./charts";
import type { DashboardPanel, MetricResult, MetricSpec } from "./dataApi";

export type ChartSeries = {
  name: string;
  color: string;
  /** Aligned with the chart's categories; null is a gap, not a zero. */
  values: (number | null)[];
  dashed?: boolean;
  opacity?: number;
};

export type PanelSeries = {
  categories: string[];
  series: ChartSeries[];
  /** The newest time bucket, left out because it is not over yet. */
  inProgress: { dim: string; bucket: string } | null;
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;
const DAY_MS = 86_400_000;

export function isIsoDate(value: string): boolean {
  return ISO_DATE.test(value) && !Number.isNaN(Date.parse(value.slice(0, 10)));
}

/** A UTC timestamp, because a bucket's date has no time zone and must not shift a day. */
export function dateMs(value: string): number {
  return Date.parse(value.slice(0, 10));
}

export function formatDate(value: string, withYear = true): string {
  return new Date(dateMs(value)).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: withYear ? "numeric" : undefined,
    timeZone: "UTC",
  });
}

/** Round axis steps (1, 2 or 5 × 10ⁿ) that include both ends. */
export function niceScale(lo: number, hi: number, count = 5): { lo: number; hi: number; ticks: number[] } {
  const top = hi === lo ? lo + 1 : hi;
  const rough = (top - lo) / Math.max(1, count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const unit = rough / magnitude;
  const step = (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * magnitude;
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(top / step) * step;
  const ticks: number[] = [];
  for (let i = 0; start + i * step <= end + step / 2; i += 1) ticks.push(Number((start + i * step).toPrecision(12)));
  return { lo: start, hi: end, ticks };
}

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

/** Short axis text. A metric's raw values: money arrives in cents. */
export function formatTick(value: number, format: MetricSpec["format"]): string {
  if (format === "currency_cents") return `€${compact.format(value / 100)}`;
  if (format === "percent") return `${compact.format(value)}%`;
  return compact.format(value);
}

/**
 * The newest bucket of a time dimension if it has not finished. Drawn as it stands, a week with
 * two days of sales reads as a collapse. A series of one or two buckets is left alone, since a
 * short period is exactly a question about the bucket in progress.
 */
export function inProgressBucket(dim: string, buckets: string[], now: Date): string | null {
  if (buckets.length < 3 || !buckets.every(isIsoDate)) return null;
  const last = [...buckets].sort().at(-1) as string;
  const start = dateMs(last);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  if (dim === "day" || dim === "date") return start >= today ? last : null;
  if (dim === "week") return start + 7 * DAY_MS > today ? last : null;
  if (dim === "month") {
    const first = new Date(start);
    return first.getUTCFullYear() === now.getFullYear() && first.getUTCMonth() === now.getMonth() ? last : null;
  }
  return null;
}

/** The part every label shares, up to a word boundary: chain names repeat it on every bar. */
export function sharedPrefix(labels: string[]): string {
  if (labels.length < 3) return "";
  let prefix = labels[0];
  for (const label of labels) {
    while (prefix && !label.startsWith(prefix)) prefix = prefix.slice(0, -1);
  }
  const boundary = prefix.lastIndexOf(" ");
  return boundary > 0 ? prefix.slice(0, boundary + 1) : "";
}

/** A metric's rows as series aligned on one list of categories. */
export function panelSeries(panel: DashboardPanel, result: MetricResult, now: Date): PanelSeries {
  const [x, split] = panel.dims;
  const rawBuckets = [...new Set(result.rows.map((r) => String(r[x])))];
  const inProgress = inProgressBucket(x, rawBuckets, now);
  const rows = inProgress ? result.rows.filter((r) => String(r[x]) !== inProgress) : result.rows;
  let categories = [...new Set(rows.map((r) => String(r[x])))];
  if (categories.every(isIsoDate)) categories = categories.sort();
  // An hour with no sales is still an hour on the axis.
  else if (/hour/.test(x) && categories.every((c) => Number.isInteger(Number(c)))) categories = Array.from({ length: 24 }, (_, h) => String(h));
  const position = new Map(categories.map((c, i) => [c, i]));
  const bucket = inProgress ? { dim: x, bucket: inProgress } : null;

  if (split) {
    // Alphabetical, so a group keeps its colour when a role or a period removes another one.
    const names = [...new Set(rows.map((r) => String(r[split])))].sort();
    const colours = colorScale(names);
    const series = names.map((name): ChartSeries => {
      const values: (number | null)[] = categories.map(() => null);
      for (const r of rows) {
        const at = position.get(String(r[x]));
        if (String(r[split]) === name && at !== undefined) values[at] = r.value ?? null;
      }
      return { name, color: colours.get(name) as string, values };
    });
    return { categories, series, inProgress: bucket };
  }

  const pick = (field: "value" | "compare_value") => {
    const values: (number | null)[] = categories.map(() => null);
    for (const r of rows) {
      const at = position.get(String(r[x]));
      if (at !== undefined) values[at] = (field === "value" ? r.value : r.compare_value) ?? null;
    }
    return values;
  };
  const period = result.period.from === result.period.to ? result.period.from : "this period";
  const series: ChartSeries[] = [{ name: period, color: CATEGORY_PALETTE[0], values: pick("value") }];
  if (panel.compare && result.compare) {
    series.push({ name: "comparison", color: "var(--dim)", values: pick("compare_value"), dashed: true, opacity: 0.55 });
  }
  return { categories, series, inProgress: bucket };
}
