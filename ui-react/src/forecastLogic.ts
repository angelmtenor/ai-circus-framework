// Pure helpers of the forecast views (ForecastViews.tsx, DroughtControlRoom.tsx).
import type { ForecastSeries } from "./apiClient";
import { CHART_COLORS } from "./charts";
import type { TimeBand, TimeLine } from "./ForecastViews";

/** A forecast series as chart lines (observed, forecast joined to the last observation) and its prediction bands. */
export function seriesLines(s: ForecastSeries, levels: string[], forecastLabel: string): { lines: TimeLine[]; bands: TimeBand[] } {
  const lastHistory = { ds: s.history.ds.slice(-1), y: s.history.y.slice(-1) };
  return {
    lines: [
      { key: "history", label: "Observed", ds: s.history.ds, values: s.history.y, color: CHART_COLORS.dim, width: 1.5 },
      // Joined to the last observation so the forecast visibly continues the series.
      { key: "forecast", label: forecastLabel, ds: [...lastHistory.ds, ...s.forecast.ds], values: [...lastHistory.y, ...s.forecast.mean], color: CHART_COLORS.accent, width: 2.5 },
    ],
    bands: [...levels]
      .sort((a, b) => Number(b) - Number(a))
      .map((level, i) => ({
        key: level,
        ds: s.forecast.ds,
        lower: s.forecast.bands[level].lower,
        upper: s.forecast.bands[level].upper,
        opacity: i === 0 ? 0.14 : 0.24,
      })),
  };
}
