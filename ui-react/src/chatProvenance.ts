import type { Message as AguiMessage } from "@ag-ui/core";
import type { DocumentTool } from "./apiClient";

/**
 * Where each chat reply came from, read off the tool calls the agent actually made in
 * that turn — never from what the model claims. Every backend tool is one source:
 * the scenario's documents (its `documents.tool`, or rag-agent/form-agent's retrieval),
 * the business rules and the model (`predict_records`, whose result carries `rules`
 * when the scenario has them, or a forecast's `get_forecast`), the model's evaluation
 * (`get_forecast_backtest` for a forecast), the dataset, or a chart/table
 * drawn in the chat. A reply that used no tool is labelled as such.
 */

export type ChatLocale = "es" | "en";
export type ChatScope = "all" | "documents";

export type ProvenanceKey =
  | "documents"
  | "rules"
  | "model"
  | "evaluation"
  | "metrics"
  | "health"
  | "data"
  | "chart"
  | "table"
  | "mcp"
  | "none";
// `mcpServers`: the MCP servers whose tools the reply used (rag-agent names them mcp__<server>__<tool>).
export type Provenance = { keys: ProvenanceKey[]; sources: string[]; documentsEmpty: boolean; mcpServers: string[] };

// The chat's "via MCP" route (docs/adr/0019): a tool an MCP server published.
const MCP_TOOL = /^mcp__(.+?)__(.+)$/;
export function mcpToolParts(name: string): { server: string; tool: string } | null {
  const m = MCP_TOOL.exec(name);
  return m ? { server: m[1], tool: m[2] } : null;
}

// Delimiters a retrieval tool's result wraps each excerpt in — rag-agent's retrieve_docs
// and graph_search and assistant's document tool write `<retrieved_document source=…>`
// (the indirect-prompt-injection guard), form-agent's catalog `<catalog_entry source=…>`;
// the older `[Source: file]` marker is still accepted for any agent that emits it.
const SOURCE_TAG = /<(?:retrieved_document|catalog_entry)\s+source="([^"]+)">|\[Source:\s*([^\]]+)\]/g;
const RETRIEVAL_TOOLS = new Set(["retrieve_docs", "read_document", "graph_search", "graph_path", "retrieve_catalog"]);
// A data product's governed tools (assistant's core/data_tools.py): metrics, health, records/SQL.
const METRIC_TOOLS = new Set(["query_metric", "list_metrics"]);
const RECORD_TOOLS = new Set(["get_record", "search_records", "run_sql", "get_dataset_sample"]);
// The synthetic answer ChatPanel writes for a frontend tool call (render_chart…).
export const FRONTEND_TOOL_ANSWER = "Displayed to the user.";

export const CHAT_WORDS = {
  es: {
    basedOn: "Respuesta basada en",
    documents: "Documentos",
    rules: "Reglas",
    model: "Modelo",
    evaluation: "Evaluación del modelo",
    metrics: "Métricas gobernadas",
    health: "Salud de los datos",
    data: "Datos (los del dashboard)",
    chart: "Gráfico",
    table: "Tabla",
    mcp: "MCP",
    none: "Sin herramientas: contexto del escenario",
    noDocuments: "sin resultados",
    allSources: "Todas las fuentes",
    only: (label: string) => `Solo ${label.toLowerCase()}`,
    searching: (label: string) => `Consultando ${label.toLowerCase()}…`,
    scopeHint: (label: string) => `Solo ${label.toLowerCase()}: el asistente no ve los datos ni el modelo.`,
  },
  en: {
    basedOn: "Answer based on",
    documents: "Documents",
    rules: "Rules",
    model: "Model",
    evaluation: "Model evaluation",
    metrics: "Governed metrics",
    health: "Data health",
    data: "Data (the dashboard's)",
    chart: "Chart",
    table: "Table",
    mcp: "MCP",
    none: "No tools: scenario context only",
    noDocuments: "nothing found",
    allSources: "All sources",
    only: (label: string) => `${label} only`,
    searching: (label: string) => `Searching ${label.toLowerCase()}…`,
    scopeHint: (label: string) => `${label} only: the assistant cannot see the data or the model.`,
  },
} as const;

export const PROVENANCE_ICONS: Record<ProvenanceKey, string> = {
  documents: "📚",
  rules: "⚖️",
  model: "🧮",
  evaluation: "🎯",
  metrics: "📐",
  health: "🩺",
  data: "📊",
  chart: "📈",
  table: "📋",
  mcp: "🔌",
  none: "💬",
};

/** "normativa/ley-39-2015-art-068-subsanacion.md" → "ley-39-2015-art-068-subsanacion". */
export function sourceName(source: string): string {
  return source.replace(/^.*\//, "").replace(/\.(md|txt|pdf)$/i, "");
}

function sourcesIn(content: string): string[] {
  return [...content.matchAll(SOURCE_TAG)].map((m) => (m[1] ?? m[2]).trim());
}

// A frontend tool that draws the reply in the chat (useCopilotAction's render_chart/render_table).
const DRAWING_TOOLS = new Set(["render_chart", "render_table"]);

/**
 * Provenance of every reply, keyed by the id of the last assistant message with text in
 * each user-delimited turn (the bubble the chips go under) — or, when the model answered
 * only by drawing a chart or table, the message that drew it, so that reply says where
 * its data came from too.
 */
export function replyProvenance(messages: AguiMessage[], documentTool?: DocumentTool | null): Map<string, Provenance> {
  const results = new Map<string, string>();
  for (const m of messages) if (m.role === "tool") results.set(m.toolCallId, typeof m.content === "string" ? m.content : "");

  const out = new Map<string, Provenance>();
  let calls: { name: string; id: string }[] = [];
  let lastReplyId: string | null = null;
  let lastDrawingId: string | null = null;
  const flush = () => {
    const anchor = lastReplyId ?? lastDrawingId;
    if (anchor) out.set(anchor, provenanceOf(calls, results, documentTool));
    calls = [];
    lastReplyId = null;
    lastDrawingId = null;
  };
  for (const m of messages) {
    if (m.role === "user") flush();
    else if (m.role === "assistant") {
      for (const call of m.toolCalls ?? []) {
        calls.push({ name: call.function.name, id: call.id });
        if (DRAWING_TOOLS.has(call.function.name)) lastDrawingId = m.id;
      }
      if (typeof m.content === "string" ? m.content.trim() : m.content) lastReplyId = m.id;
    }
  }
  flush();
  return out;
}

function provenanceOf(
  calls: { name: string; id: string }[],
  results: Map<string, string>,
  documentTool?: DocumentTool | null,
): Provenance {
  const keys = new Set<ProvenanceKey>();
  const sources = new Set<string>();
  const mcpServers = new Set<string>();
  let documentCalls = 0;
  for (const { name, id } of calls) {
    const result = results.get(id) ?? "";
    const mcp = mcpToolParts(name);
    if (mcp) {
      keys.add("mcp");
      mcpServers.add(mcp.server);
      for (const source of sourcesIn(result)) sources.add(source);
    } else if (name === "list_sources") keys.add("documents"); // the catalogue: no passages to cite
    else if (name === documentTool?.name || RETRIEVAL_TOOLS.has(name)) {
      keys.add("documents");
      documentCalls += 1;
      for (const source of sourcesIn(result)) sources.add(source);
    } else if (name === "predict_records") {
      if (result.includes('"rules"')) keys.add("rules");
      keys.add("model");
    } else if (name === "get_forecast") keys.add("model");
    else if (name === "get_predictions_vs_actuals" || name === "get_forecast_backtest") keys.add("evaluation");
    else if (METRIC_TOOLS.has(name)) keys.add("metrics");
    else if (name === "data_health") keys.add("health");
    else if (RECORD_TOOLS.has(name)) keys.add("data");
    else if (name === "render_chart") keys.add("chart");
    else if (name === "render_table") keys.add("table");
  }
  if (keys.size === 0) keys.add("none");
  const order: ProvenanceKey[] = ["mcp", "documents", "rules", "model", "evaluation", "metrics", "health", "data", "chart", "table", "none"];
  return {
    keys: order.filter((k) => keys.has(k)),
    sources: [...sources],
    documentsEmpty: documentCalls > 0 && sources.size === 0,
    mcpServers: [...mcpServers],
  };
}
