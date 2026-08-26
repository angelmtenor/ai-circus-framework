/**
 * The two frontend "tools" a chat agent can call to show rich UI instead of prose —
 * registered via useCopilotAction (so their JSON-schema-able `parameters` can be
 * converted into AG-UI Tool definitions, see ChatPanel.tsx) but *dispatched* by
 * ChatPanel itself (reading the same registry back via useCopilotContext()), not by
 * CopilotKit's own message-tree renderer — this app drives the chat transport
 * directly against @ag-ui/client's HttpAgent rather than useCopilotChat (see
 * ChatPanel.tsx's top-of-file note for why), so the render-on-tool-call wiring has
 * to be manual too. useCopilotAction is still the right registration API: it's the
 * same long-standing hook, independent of the newer runtime-client machinery.
 */
import { useCopilotAction } from "@copilotkit/react-core";
import type { ReactNode } from "react";
import { ChatTable } from "./ChatTable";
import { PlotlyChart } from "./PlotlyChart";
import type { PlotlyDatum, PlotlyLayout } from "./plotly";

export type ChatChartSeries = { name?: string; x_categories?: string[]; x_values?: number[]; y?: number[]; z?: number[] };
export type ChatChartArgs = {
  chart_type: "bar" | "line" | "scatter" | "scatter3d" | "histogram" | "pie";
  title?: string;
  x_label?: string;
  y_label?: string;
  series: ChatChartSeries[];
};
export type ChatTableArgs = { title?: string; columns: string[]; rows: Record<string, string | number>[] };

const PALETTE = ["#6366f1", "#22c55e", "#f59e0b", "#ec4899", "#06b6d4", "#ef4444"];

function seriesX(s: ChatChartSeries): (number | string)[] {
  return s.x_values ?? s.x_categories ?? [];
}

function buildChatChartTraces(args: ChatChartArgs): { data: PlotlyDatum[]; layout: PlotlyLayout } {
  const { chart_type, series } = args;
  // Fall back rather than trusting the model to always honor the (required)
  // x_label/y_label parameters — some models still omit optional-looking
  // fields, and an unlabeled axis is worse than a generic one.
  const x_label = args.x_label?.trim() || "x";
  const y_label = args.y_label?.trim() || "y";
  const axes: PlotlyLayout =
    chart_type === "scatter3d"
      ? { scene: { xaxis: { title: { text: x_label } }, yaxis: { title: { text: y_label } }, zaxis: { title: { text: "z" } }, dragmode: "orbit" } }
      : { xaxis: { title: { text: x_label } }, yaxis: { title: { text: y_label } } };

  if (chart_type === "pie") {
    const s = series[0];
    return { data: [{ type: "pie", labels: seriesX(s), values: s?.y ?? [], hole: 0.35, marker: { colors: PALETTE } }], layout: {} };
  }

  const data: PlotlyDatum[] = series.map((s, i) => {
    const color = PALETTE[i % PALETTE.length];
    const x = seriesX(s);
    switch (chart_type) {
      case "histogram":
        return { type: "histogram", x, name: s.name, marker: { color } };
      case "bar":
        return { type: "bar", x, y: s.y, name: s.name, marker: { color } };
      case "line":
        return { type: "scatter", mode: "lines+markers", x, y: s.y, name: s.name, line: { color }, marker: { color } };
      case "scatter3d":
        return { type: "scatter3d", mode: "markers", x, y: s.y, z: s.z, name: s.name, marker: { size: 4, color } };
      default:
        return { type: "scattergl", mode: "markers", x, y: s.y, name: s.name, marker: { size: 6, color } };
    }
  });
  return { data, layout: { ...axes, showlegend: series.length > 1, barmode: "group" } };
}

function ChatChart({ args }: { args: ChatChartArgs }) {
  const { data, layout } = buildChatChartTraces(args);
  return (
    <div className="chat-chart">
      {args.title && <div className="chat-table-title">{args.title}</div>}
      <PlotlyChart data={data} layout={layout} height={args.chart_type === "scatter3d" ? 360 : 300} />
    </div>
  );
}

/**
 * Registers render_chart/render_table with the surrounding <CopilotKit> provider.
 * Call once per workspace (RagView / TabularView), alongside the useCopilotReadable
 * calls that share dashboard state with the same agent.
 */
// useCopilotAction's `render` type is CopilotKit's own message-tree-renderer contract
// (a narrow ActionRenderProps union expecting a ReactElement back); we invoke it
// ourselves instead (see ChatPanel.tsx), so a plain ReactNode-returning function is
// cast through unknown here rather than fought into that exact shape.
type LooseAction = Parameters<typeof useCopilotAction>[0];

// A `handler` is mandatory here — confirmed empirically: useCopilotAction's internal
// getActionConfig() throws "Invalid action configuration" for a render-only action
// with no `handler`/`available`/renderAndWait*, since it has no branch for that shape.
// A no-op is correct, not a workaround: this app never lets CopilotKit's own runtime
// invoke it (see ChatPanel.tsx) — the "work" of these actions is entirely the render.
async function noopHandler(): Promise<void> {}

export function useChatGenerativeUiActions(): void {
  useCopilotAction({
    name: "render_chart",
    description:
      "Render a chart from data you already have (retrieved from documents, computed, or otherwise known) — " +
      "use this instead of describing numbers in prose whenever the question calls for a plot. Supports 3D " +
      "scatter plots via chart_type='scatter3d' (provide x, y, and z per series).",
    parameters: [
      { name: "chart_type", type: "string", enum: ["bar", "line", "scatter", "scatter3d", "histogram", "pie"], required: true },
      { name: "title", type: "string", required: false },
      { name: "x_label", type: "string", required: true, description: "Axis label for x — always provide a descriptive one, never omit." },
      { name: "y_label", type: "string", required: true, description: "Axis label for y — always provide a descriptive one, never omit." },
      {
        name: "series",
        type: "object[]",
        required: true,
        attributes: [
          { name: "name", type: "string", required: false },
          { name: "x_values", type: "number[]", required: false, description: "Numeric x values — use for scatter/line/scatter3d/histogram." },
          { name: "x_categories", type: "string[]", required: false, description: "Category labels for x — use for bar/pie." },
          { name: "y", type: "number[]", required: false },
          { name: "z", type: "number[]", required: false, description: "Only for chart_type='scatter3d'." },
        ],
      },
    ],
    handler: noopHandler,
    render: (({ args }: { args: ChatChartArgs }): ReactNode => <ChatChart args={args} />) as never,
  } as LooseAction);

  useCopilotAction({
    name: "render_table",
    description: "Render tabular data as a real, sortable table — use this instead of a markdown table for structured data.",
    parameters: [
      { name: "title", type: "string", required: false },
      { name: "columns", type: "string[]", required: true },
      { name: "rows", type: "object[]", required: true, description: "One object per row, keyed by column name." },
    ],
    handler: noopHandler,
    render: (({ args }: { args: ChatTableArgs }): ReactNode => <ChatTable title={args.title} columns={args.columns ?? []} rows={args.rows ?? []} />) as never,
  } as LooseAction);
}
