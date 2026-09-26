import { lazy, Suspense, type ComponentProps } from "react";

// plotly.js-dist-min is the single largest dependency in the bundle (~3.5 MB minified),
// so it is fetched the first time a chart actually renders — not on the login screen or
// scenario picker. Same props as the real component (PlotlyChartImpl.tsx).
const PlotlyChartImpl = lazy(() => import("./PlotlyChartImpl").then((m) => ({ default: m.PlotlyChart })));

export function PlotlyChart(props: ComponentProps<typeof PlotlyChartImpl>) {
  return (
    <Suspense fallback={<div style={{ height: props.height ?? 320 }} aria-busy="true" />}>
      <PlotlyChartImpl {...props} />
    </Suspense>
  );
}
