import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { PlotlyDatum, PlotlyLayout } from "./plotly";
import { useTheme } from "./useTheme";

// Bound to plotly.js-dist-min (a single prebuilt bundle, incl. gl3d for scatter3d)
// rather than react-plotly.js's own default export, which pulls the separate,
// heavier "plotly.js" package instead — see plotly.d.ts for why.
const Plot = createPlotlyComponent(Plotly);

function mergeAxis(themeAxis: unknown, chartAxis: unknown): PlotlyLayout {
  return { ...(themeAxis as PlotlyLayout | undefined), ...(chartAxis as PlotlyLayout | undefined) };
}

/** A plain `{...a, ...b}` spread would let a 3D chart's own `scene` (chartBuilder.ts
 * only ever sets `scene.{x,y,z}axis.title`) wholesale replace — not merge with — the
 * theme's `scene` (gridcolor/backgroundcolor/color, see themes/tron.ts), since object
 * spread only merges top-level keys. Losing that per-axis styling is why 3D plots were
 * rendering with Plotly's default white/opaque panes instead of the dark theme's
 * transparent ones, looking like they "pop in front of" the rest of the UI. */
function mergeLayout(themeLayout: PlotlyLayout, chartLayout: PlotlyLayout): PlotlyLayout {
  const themeScene = (themeLayout.scene ?? {}) as PlotlyLayout;
  const chartScene = (chartLayout.scene ?? {}) as PlotlyLayout;
  return {
    ...themeLayout,
    ...chartLayout,
    xaxis: mergeAxis(themeLayout.xaxis, chartLayout.xaxis),
    yaxis: mergeAxis(themeLayout.yaxis, chartLayout.yaxis),
    ...(themeLayout.scene || chartLayout.scene
      ? {
          scene: {
            ...themeScene,
            ...chartScene,
            xaxis: mergeAxis(themeScene.xaxis, chartScene.xaxis),
            yaxis: mergeAxis(themeScene.yaxis, chartScene.yaxis),
            zaxis: mergeAxis(themeScene.zaxis, chartScene.zaxis),
          },
        }
      : {}),
  };
}

/**
 * Theme-aware wrapper for the Data dashboard's flexible chart builder (see
 * chartBuilder.ts) — merges the active theme's plotlyLayout (transparent
 * background, gridline/font colors, colorway) with the chart-specific layout
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
      layout={{
        ...mergeLayout(theme.plotlyLayout, layout ?? {}),
        height,
        autosize: true,
        // Without this, Plotly.react() resets any user-driven camera rotation/zoom/pan
        // back to its default on every re-render whose layout object is a fresh
        // reference (e.g. a sibling chart card's config changing) — a fixed
        // uirevision tells it "same interaction session, keep what the user set".
        uirevision: "keep",
      }}
      config={{ displayModeBar: false, responsive: true }}
      className="plotly-chart"
    />
  );
}
