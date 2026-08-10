import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { PlotlyDatum, PlotlyLayout } from "./plotly";
import { useTheme } from "./useTheme";

// Bound to plotly.js-dist-min (a single prebuilt bundle, incl. gl3d for scatter3d)
// rather than react-plotly.js's own default export, which pulls the separate,
// heavier "plotly.js" package instead — see plotly.d.ts for why.
const Plot = createPlotlyComponent(Plotly);

/**
 * Theme-aware wrapper for the Data dashboard's flexible chart builder (see
 * chartBuilder.ts) — applies the active theme's plotlyLayout (transparent
 * background, gridline/font colors, colorway) underneath the chart-specific layout
 * chartBuilder produces, so callers never have to remember to theme a chart
 * themselves. Every fixed-purpose analytical chart elsewhere in the app still uses
 * the hand-rolled SVG primitives in charts.tsx.
 */
export function PlotlyChart({
  data,
  layout,
  height = 320,
}: {
  data: PlotlyDatum[];
  layout?: PlotlyLayout;
  height?: number;
}) {
  const { theme } = useTheme();
  return (
    <Plot
      data={data}
      layout={{ ...theme.plotlyLayout, ...layout, height, autosize: true }}
      config={{ displayModeBar: false, responsive: true }}
      useResizeHandler
      className="plotly-chart"
    />
  );
}
