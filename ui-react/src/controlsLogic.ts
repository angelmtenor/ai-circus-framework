// Pure helpers of the shared parameter controls (controls.tsx): histogram bins, extents, dates.

const BINS = 28;

/** Counts of `values` in `bins` equal-width bins over [min, max] (values outside are ignored). */
export function histogramCounts(values: number[], min: number, max: number, bins = BINS): number[] {
  const counts = new Array<number>(bins).fill(0);
  if (!(max > min)) return counts;
  const width = (max - min) / bins;
  for (const v of values) {
    if (!Number.isFinite(v) || v < min || v > max) continue;
    counts[Math.min(bins - 1, Math.floor((v - min) / width))] += 1;
  }
  return counts;
}

/** The [lo, hi] extent of the finite values, or null when there are none. */
export function extent(values: number[]): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (!Number.isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return lo <= hi ? [lo, hi] : null;
}

/** Snap a data extent outwards to the step grid, so the slider's ends include every value. */
export function snapExtent([lo, hi]: [number, number], step: number): [number, number] {
  const s = step > 0 ? step : 1;
  const snappedLo = Math.floor(lo / s) * s;
  const snappedHi = Math.ceil(hi / s) * s;
  // Rounding keeps e.g. 0.30000000000000004 off the labels.
  const digits = Math.max(0, Math.min(6, Math.ceil(-Math.log10(s))));
  const round = (v: number) => Number(v.toFixed(digits));
  return [round(snappedLo), round(snappedHi === snappedLo ? snappedLo + s : snappedHi)];
}

const DAY_MS = 86_400_000;

/** A time column's value as whole days since the epoch (null when it is not a date). */
export function toEpochDay(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const t = Date.parse(String(value));
  return Number.isNaN(t) ? null : Math.floor(t / DAY_MS);
}

/** An epoch day as a short, readable date. */
export function formatEpochDay(day: number): string {
  return new Date(day * DAY_MS).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}
