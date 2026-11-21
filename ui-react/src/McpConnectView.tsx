// "Connect via MCP" (docs/adr/0019, 0031): how to reach a scenario's MCP door from any MCP client — a
// document scenario's on rag-agent, a data product's on data-api, a tabular model's on the assistant — plus a live check that runs the
// same tools/list an MCP client would, with the caller's token and headers.
// Static DOM only: nothing animates, so the tab stays quiet when idle.
import { useState, type ReactNode } from "react";

import { listMcpTools, type McpTool } from "./apiClient";
import { config } from "./config";

const TOOL_HELP: Record<string, string> = {
  list_sources: "Which documents are indexed, and how many passages each has.",
  search_documents: "The most similar passages for a question, each with its source.",
  read_document: "One document's passages, in order.",
  list_metrics: "The governed metrics and record entities this role may query.",
  query_metric: "A governed metric for a period, split, filtered and compared.",
  search_records: "Records as received, by text or key prefix.",
  get_record: "One record with its processing trail.",
  data_health: "Freshness vs SLO, the last transform, ops health and your role.",
  run_sql: "Read-only SQL over the curated datasets (SQL roles only).",
  describe_model: "What the model predicts, from which inputs, how well, and what it is for.",
  score_record: "Score one record and explain it: the prediction and what pushed it.",
  drought_state: "A supply system's state against the drought plan: now, on a past date, or ahead.",
  storage_forecast: "The storage forecast (p10, p50, p90) of a system or a reservoir, and its drivers.",
  what_if: "Storage under a rainfall scenario and demand savings, with the days to each threshold.",
  least_restrictive_measures: "The least restrictive package of measures that keeps emergency unlikely.",
  search_drought_plan: "The drought plan's paragraphs for a question, each with its article.",
  data_quality: "Readings of a reservoir that need review, with what each flag means.",
  weekly_bulletin: "This week's drought bulletin, as markdown.",
};

type Check = { state: "idle" | "checking" } | { state: "ok"; tools: McpTool[] } | { state: "error"; message: string };

function CopyBlock({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard blocked (insecure origin or permission): the text stays selectable.
    }
  }
  return (
    <div className="mcp-copy">
      <div className="mcp-copy-head">
        <span>{label}</span>
        <button type="button" className="mcp-copy-btn" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="mcp-code">{text}</pre>
    </div>
  );
}

export function McpConnectView({
  scenarioSlug,
  exposed,
  accessToken,
  baseUrl = config.ragAgentUrl,
  localPort = 8031,
  headers = {},
  title = "Use these documents from any MCP client",
  lede,
  owns = "documents",
  emptyHint = (
    <>
      Empty results mean nothing is indexed yet: run <code>make k3s-vectorize</code>.
    </>
  ),
}: {
  scenarioSlug: string;
  exposed: string[];
  accessToken: string | null;
  /** The service serving the door (rag-agent by default, data-api for a data product). */
  baseUrl?: string;
  /** `make k3s-mcp-portforward`'s localhost port for that service (8031 rag-agent, 8032 data-api, 8034 assistant). */
  localPort?: number;
  /** Extra headers every request carries (a data product's X-Data-View-As). */
  headers?: Record<string, string>;
  title?: string;
  lede?: ReactNode;
  /** What each organisation sees only its own of, in the footnote. */
  owns?: string;
  /** The footnote's last sentence: what an empty or failed result usually means. */
  emptyHint?: ReactNode;
}) {
  const [check, setCheck] = useState<Check>({ state: "idle" });
  const url = `${baseUrl}/mcp/${scenarioSlug}`;
  // On the local cluster `*.localhost` resolves in browsers and curl but not for every client
  // (Node or Python on WSL), so clients use `make k3s-mcp-portforward`'s localhost port.
  const localCluster = new URL(baseUrl).hostname.endsWith(".localhost");
  const clientUrl = localCluster ? `http://localhost:${localPort}/mcp/${scenarioSlug}` : url;
  const name = scenarioSlug.replace(/_/g, "-");
  const extra = Object.entries(headers);
  const claudeCode = [
    `claude mcp add --transport http ${name} ${clientUrl}`,
    `  --header "Authorization: Bearer $AI_CIRCUS_TOKEN"`,
    ...extra.map(([k, v]) => `  --header "${k}: ${v}"`),
  ].join(" \\\n");
  const json = JSON.stringify(
    {
      mcpServers: {
        [name]: { type: "http", url: clientUrl, headers: { Authorization: "Bearer ${AI_CIRCUS_TOKEN}", ...headers } },
      },
    },
    null,
    2,
  );

  async function runCheck() {
    setCheck({ state: "checking" });
    try {
      setCheck({ state: "ok", tools: await listMcpTools(baseUrl, scenarioSlug, accessToken, headers) });
    } catch (e) {
      setCheck({ state: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }

  return (
    <div className="mcp-connect panel-card">
      <h3>{title}</h3>
      {lede ?? (
        <>
          <p className="mcp-lede">
            This chat can use them too: pick <strong>🔌 Via MCP</strong> under the chat box, and its assistant loads these
            tools from the endpoint below instead of reading the documents directly.
          </p>
          <p className="mcp-lede">
            Claude Code, Claude Desktop, VS Code, MCP Inspector or your own agent can search and read this scenario's
            documents for your organisation. Read-only, and no model runs here: your client's model does the reasoning.
          </p>
        </>
      )}

      <div className="mcp-grid">
        <section>
          <h4>Endpoint</h4>
          <CopyBlock label="Streamable HTTP" text={url} />
          <h4>Tools</h4>
          <ul className="mcp-tools">
            {exposed.map((tool) => (
              <li key={tool}>
                <code>{tool}</code>
                <span>{TOOL_HELP[tool] ?? ""}</span>
              </li>
            ))}
          </ul>
          <h4>Check it with your session</h4>
          <div className="mcp-check">
            <button type="button" className="btn-secondary" onClick={runCheck} disabled={check.state === "checking"}>
              {check.state === "checking" ? "Checking…" : "Run tools/list"}
            </button>
            {check.state === "ok" && (
              <span className="mcp-ok">Connected: {check.tools.map((t) => t.name).join(", ")}</span>
            )}
            {check.state === "error" && <span className="mcp-err">{check.message}</span>}
          </div>
        </section>
        <section>
          <h4>Add it to a client</h4>
          {localCluster && (
            <p className="mcp-note">
              On this local cluster, run <code>make k3s-mcp-portforward</code> once: it keeps the door on{" "}
              <code>localhost:{localPort}</code> across reboots, which every client can reach.
            </p>
          )}
          <CopyBlock label="Claude Code" text={claudeCode} />
          <CopyBlock label="mcp.json (Claude Desktop, VS Code, …)" text={json} />
          <p className="mcp-note">
            <code>AI_CIRCUS_TOKEN</code> is the same bearer token the app uses: a demo tenant's API key, or a Keycloak access
            token (it expires within minutes). The tenant comes from the token, so each organisation sees only its own{" "}
            {owns}. {emptyHint}
          </p>
        </section>
      </div>
    </div>
  );
}
