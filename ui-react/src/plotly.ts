import type { CSSProperties } from "react";

/**
 * Shared Plotly-adjacent types, used by chartBuilder.ts (which stays free of any
 * plotly.js import) and PlotlyChart.tsx/plotly-ambient.d.ts. Deliberately loose
 * (`Record<string, unknown>`) rather than the full plotly.js type surface — this
 * app only ever builds a handful of trace/layout shapes (see chartBuilder.ts).
 */
export type PlotlyDatum = Record<string, unknown>;
export type PlotlyLayout = Record<string, unknown>;

export type PlotParams = {
  data: PlotlyDatum[];
  layout?: PlotlyLayout;
  config?: Record<string, unknown>;
  style?: CSSProperties;
  className?: string;
  useResizeHandler?: boolean;
  onError?: (err: unknown) => void;
  onInitialized?: (figure: unknown, graphDiv: HTMLDivElement) => void;
  onUpdate?: (figure: unknown, graphDiv: HTMLDivElement) => void;
};
