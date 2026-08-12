import { useEffect, useMemo, useRef } from "react";
import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { PlotlyDatum, PlotlyLayout } from "./plotly";
import { useTheme } from "./useTheme";

// Bound to plotly.js-dist-min (a single prebuilt bundle, incl. gl3d for scatter3d)
// rather than react-plotly.js's own default export, which pulls the separate,
// heavier "plotly.js" package instead — see plotly.d.ts for why.
const Plot = createPlotlyComponent(Plotly);
const PlotlyResize = Plotly as unknown as { Plots: { resize: (gd: HTMLDivElement) => void } };

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
  /** Number (px) or any CSS height value (e.g. "70vh" for a maximized card). Applied
   * via style, plus an explicit resize (see the effect below) so the plot's internal
   * canvas/camera math is recalibrated to match — not react-plotly.js's own
   * `useResizeHandler` or `config.responsive`, see why below. */
  height?: number | string;
}) {
  const { theme } = useTheme();
  const gdRef = useRef<HTMLDivElement | null>(null);
  // react-plotly.js re-runs Plotly.react() (a full update, not just a resize) whenever
  // any prop's *reference* changes — including layout/style, which were previously
  // recreated as fresh object literals on every render. Since Plotly.react() commits
  // whatever camera/zoom is in `layout` right now, and a gl3d drag only writes the
  // in-progress camera back once the gesture ends (mouseup), a re-render mid-drag
  // (e.g. a sibling card's own state changing) would call Plotly.react() with the
  // *last-committed* camera and visibly snap the still-uncommitted drag back to it.
  // Memoizing on the actual content, not just object identity, keeps these stable
  // across unrelated re-renders so Plotly.react() only fires when the chart itself
  // truly changed.
  const mergedLayout = useMemo(
    () => ({
      ...mergeLayout(theme.plotlyLayout, layout ?? {}),
      autosize: true,
      // A fixed uirevision tells Plotly "same interaction session, keep what the user
      // set" instead of resetting camera/zoom/pan on every legitimate layout update too.
      uirevision: "keep",
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(theme.plotlyLayout), JSON.stringify(layout)],
  );
  const style = useMemo(() => ({ width: "100%", height }), [height]);

  // Deliberately not `config.responsive`/`useResizeHandler` — both keep a resize
  // listener permanently active (a ResizeObserver on the plot's own container for the
  // former, a window "resize" listener for the latter) for the plot's whole lifetime.
  // A gl3d orbit drag only writes its camera back into Plotly's own state on mouse-up;
  // any resize firing while the button is still held forces a redraw from that
  // not-yet-updated state, which is what made a held drag visibly snap back toward
  // where it started. Resizing only explicitly, exactly when `height` itself changes
  // (grid card ⇄ maximized overlay), keeps the canvas calibrated for that transition
  // without any listener left running during ordinary interaction.
  useEffect(() => {
    if (gdRef.current) PlotlyResize.Plots.resize(gdRef.current);
  }, [height]);

  return (
    <Plot
      data={data}
      layout={mergedLayout}
      config={{ displayModeBar: false }}
      style={style}
      className="plotly-chart"
      onInitialized={(_figure: unknown, gd: HTMLDivElement) => {
        gdRef.current = gd;
      }}
    />
  );
}
