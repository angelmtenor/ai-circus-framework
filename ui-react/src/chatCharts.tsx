/**
 * Renders a ```chart fenced block from a chat reply using the same SVG chart
 * primitives as the dashboard views (charts.tsx) — so an LLM reply can embed a real
 * plot by emitting JSON instead of the text-only markdown subset (markdown.tsx).
 *
 * Convention: a fenced block tagged `chart` containing JSON `{"type": ..., ...}`.
 * Unknown types or malformed JSON return null so the caller falls back to a plain
 * code block — a bad chart spec should never break the rest of the reply.
 */

import type { ReactNode } from "react";
import { BarList, CategoryBars, Gauge, Histogram, LineChart, ScatterPlot } from "./charts";

export function renderChatChart(raw: string): ReactNode | null {
  let spec: Record<string, unknown>;
  try {
    spec = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!spec || typeof spec !== "object") return null;

  switch (spec.type) {
    case "bar":
      if (!Array.isArray(spec.items)) return null;
      return <BarList items={spec.items as { label: string; value: number }[]} signed={Boolean(spec.signed)} />;

    case "scatter":
      if (!Array.isArray(spec.points)) return null;
      return (
        <ScatterPlot
          points={spec.points as { x: number; y: number }[]}
          xLabel={typeof spec.xLabel === "string" ? spec.xLabel : "x"}
          yLabel={typeof spec.yLabel === "string" ? spec.yLabel : "y"}
          sharedDomain={Boolean(spec.sharedDomain)}
          refLine={Boolean(spec.refLine)}
        />
      );

    case "line":
      if (!Array.isArray(spec.points)) return null;
      return (
        <LineChart
          points={spec.points as { x: number; y: number; lower?: number; upper?: number }[]}
          xLabel={typeof spec.xLabel === "string" ? spec.xLabel : "x"}
          yLabel={typeof spec.yLabel === "string" ? spec.yLabel : "y"}
        />
      );

    case "histogram":
      if (!Array.isArray(spec.values)) return null;
      return (
        <Histogram
          values={spec.values as number[]}
          xLabel={typeof spec.xLabel === "string" ? spec.xLabel : undefined}
          bins={typeof spec.bins === "number" ? spec.bins : undefined}
        />
      );

    case "category":
      if (!Array.isArray(spec.items)) return null;
      return <CategoryBars items={spec.items as { category: string; score: number; n?: number }[]} />;

    case "gauge":
      if (typeof spec.value !== "number") return null;
      return <Gauge value={spec.value} label={typeof spec.label === "string" ? spec.label : undefined} />;

    default:
      return null;
  }
}
