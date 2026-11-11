// "Connect via MCP" (docs/adr/0019): how to reach this scenario's documents from any MCP client,
// plus a live check that runs the same tools/list an MCP client would, with the caller's token.
// Static DOM only: nothing animates, so the tab stays quiet when idle.
import { useState } from "react";

import { listMcpTools, type McpTool } from "./apiClient";
import { config } from "./config";

const TOOL_HELP: Record<string, string> = {
  list_sources: "Which documents are indexed, and how many passages each has.",
  search_documents: "The most similar passages for a question, each with its source.",
  read_document: "One document's passages, in order.",
};

// `make k3s-mcp-portforward`'s default (RAG_AGENT_MCP_PORT).
const LOCAL_MCP_PORT = 8031;

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

export function McpConnectView({ scenarioSlug, exposed, accessToken }: { scenarioSlug: string; exposed: string[]; accessToken: string | null }) {
  const [check, setCheck] = useState<Check>({ state: "idle" });
  const url = `${config.ragAgentUrl}/mcp/${scenarioSlug}`;
  // On the local cluster `*.localhost` resolves in browsers and curl but not for every client
  // (Node or Python on WSL), so clients use `make k3s-mcp-portforward`'s localhost port.
  const localCluster = new URL(config.ragAgentUrl).hostname.endsWith(".localhost");
  const clientUrl = localCluster ? `http://localhost:${LOCAL_MCP_PORT}/mcp/${scenarioSlug}` : url;
  const name = scenarioSlug.replace(/_/g, "-");
  const claudeCode = `claude mcp add --transport http ${name} ${clientUrl} \\\n  --header "Authorization: Bearer $AI_CIRCUS_TOKEN"`;
  const json = JSON.stringify(
    { mcpServers: { [name]: { type: "http", url: clientUrl, headers: { Authorization: "Bearer ${AI_CIRCUS_TOKEN}" } } } },
    null,
    2,
  );

  async function runCheck() {
    setCheck({ state: "checking" });
    try {
      setCheck({ state: "ok", tools: await listMcpTools(config.ragAgentUrl, scenarioSlug, accessToken) });
    } catch (e) {
      setCheck({ state: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }

  return (
    <div className="mcp-connect panel-card">
      <h3>Use these documents from any MCP client</h3>
      <p className="mcp-lede">
        This chat can use them too: pick <strong>🔌 Via MCP</strong> under the chat box, and its assistant loads these
        tools from the endpoint below instead of reading the documents directly.
      </p>
      <p className="mcp-lede">
        Claude Code, Claude Desktop, VS Code, MCP Inspector or your own agent can search and read this scenario's documents
        for your organisation. Read-only, and no model runs here: your client's model does the reasoning.
      </p>

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
              On this local cluster, run <code>make k3s-mcp-portforward</code> once: it keeps rag-agent on{" "}
              <code>localhost:{LOCAL_MCP_PORT}</code> across reboots, which every client can reach.
            </p>
          )}
          <CopyBlock label="Claude Code" text={claudeCode} />
          <CopyBlock label="mcp.json (Claude Desktop, VS Code, …)" text={json} />
          <p className="mcp-note">
            <code>AI_CIRCUS_TOKEN</code> is the same bearer token the app uses: a demo tenant's API key, or a Keycloak access
            token (it expires within minutes). The tenant comes from the token, so each organisation sees only its own
            documents. Empty results mean nothing is indexed yet: run <code>make k3s-vectorize</code>.
          </p>
        </section>
      </div>
    </div>
  );
}
