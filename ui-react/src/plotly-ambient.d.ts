/**
 * Minimal local ambient module declarations for plotly.js-dist-min/react-plotly.js —
 * deliberately not the community @types/react-plotly.js package, which lags
 * plotly.js's own releases and risks a stale/incompatible shape against React 19.
 * This file must stay free of any top-level import/export of its own (only imports
 * nested inside a `declare module` block) — otherwise TypeScript treats these as
 * module *augmentations* of an already-typed module rather than full ambient
 * declarations, which fails here since neither package ships its own types.
 */

declare module "plotly.js-dist-min" {
  const Plotly: unknown;
  export default Plotly;
}

// react-plotly.js's default export binds to the (heavier, non-prebuilt) "plotly.js"
// package; we instead use its factory export bound to plotly.js-dist-min (see
// PlotlyChart.tsx), so only the factory needs a declaration.
declare module "react-plotly.js/factory" {
  import type { ComponentType } from "react";
  import type { PlotParams } from "./plotly";

  export default function createPlotlyComponent(plotly: unknown): ComponentType<PlotParams>;
}
