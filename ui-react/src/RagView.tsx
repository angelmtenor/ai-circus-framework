import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import type { ChatModel, KnowledgeGraphTrace, ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";
import { ConversationSidebar } from "./ConversationSidebar";
import { Icon } from "./Icon";
import { useChatGenerativeUiActions } from "./chatGenerativeUi";
import { ScenarioOverview } from "./ScenarioOverview";
import { useStoryDemo } from "./storyWorkspace";
import { useConversation } from "./useConversation";
import { useScenarioAgent } from "./useScenarioAgent";

// Canvas + d3-force: loaded only for a scenario that ships a knowledge graph.
const KnowledgeGraphView = lazy(() => import("./KnowledgeGraphView"));
const McpConnectView = lazy(() => import("./McpConnectView").then((m) => ({ default: m.McpConnectView })));
const SourceLibraryView = lazy(() => import("./SourceLibraryView").then((m) => ({ default: m.SourceLibraryView })));

// The chat | graph split: the chat's share of the width, dragged on the divider and
// remembered per browser.
const SPLIT_STORAGE_KEY = "ai-circus.rag-split-chat-pct";
const SPLIT_DEFAULT = 40;
const SPLIT_MIN = 24;
const SPLIT_MAX = 70;

function storedSplit(): number {
  try {
    const v = Number(localStorage.getItem(SPLIT_STORAGE_KEY));
    return v >= SPLIT_MIN && v <= SPLIT_MAX ? v : SPLIT_DEFAULT;
  } catch {
    return SPLIT_DEFAULT;
  }
}

/**
 * conversational_rag workspace — a full-page ChatGPT-style window grounded in the
 * scenario's vectorized documents, with cited sources per reply. `ui_extras` may add a
 * knowledge graph beside the chat, or a tab to browse the documents (SourceLibraryView).
 */
export function RagView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [chatModel, setChatModel] = useState<ChatModel | null>(null);
  const conversation = useConversation(config.ragAgentUrl, scenario.slug, accessToken);
  const agent = useScenarioAgent(config.ragAgentUrl, scenario.slug, conversation.conversationId, accessToken);
  // A fresh object literal here would make <CopilotKit> see a "changed" selfManagedAgents
  // prop on every re-render of RagView (e.g. every chatModel update) and reset its
  // internal registry — wiping out useChatGenerativeUiActions' registrations right
  // before ChatPanel's next send() reads them. Confirmed empirically (tools: [] at
  // send() time) before memoizing this.
  const selfManagedAgents = useMemo(() => ({ [scenario.slug]: agent }), [scenario.slug, agent]);

  return (
    <CopilotKit selfManagedAgents={selfManagedAgents}>
      <RagViewContent
        scenario={scenario}
        accessToken={accessToken}
        agent={agent}
        chatModel={chatModel}
        onModel={setChatModel}
        conversation={conversation}
      />
    </CopilotKit>
  );
}

function RagViewContent({
  scenario,
  accessToken,
  agent,
  chatModel,
  onModel,
  conversation,
}: {
  scenario: ScenarioSummary;
  accessToken: string | null;
  agent: ReturnType<typeof useScenarioAgent>;
  chatModel: ChatModel | null;
  onModel: (model: ChatModel) => void;
  conversation: ReturnType<typeof useConversation>;
}) {
  useChatGenerativeUiActions();
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);
  const graphExtras = scenario.ui_extras?.kind === "knowledge_graph" ? scenario.ui_extras : null;
  const libraryExtras = scenario.ui_extras?.kind === "source_library" ? scenario.ui_extras : null;
  const mcpTools = scenario.mcp?.expose.length ? scenario.mcp.expose : null;
  const story = scenario.story ?? null;
  const hasTabs = Boolean(story || libraryExtras || mcpTools);
  // A scenario with a story opens on its Overview.
  const [tab, setTabState] = useState<"overview" | "chat" | "library" | "mcp">(story ? "overview" : "chat");
  // A question sent to the chat from the Overview or the guided demo (ChatPanel's `prompt`), dropped when the
  // chat is left so coming back never sends it twice.
  const [prompt, setPrompt] = useState<{ id: number; text: string } | null>(null);
  const setTab = useCallback((next: "overview" | "chat" | "library" | "mcp") => {
    setTabState(next);
    if (next !== "chat") setPrompt(null);
  }, []);
  const storyTabs = useMemo(() => {
    const tabs: Record<string, string> = { overview: "Overview", assistant: libraryExtras?.chat_tab_label ?? "Chat" };
    if (libraryExtras) tabs.library = libraryExtras.tab_label;
    if (mcpTools) tabs.mcp = "Connect via MCP";
    return tabs;
  }, [libraryExtras, mcpTools]);
  const openStoryTab = (t: string) => setTab(t === "assistant" ? "chat" : (t as "overview" | "library" | "mcp"));
  const ask = (text: string) => {
    setTab("chat");
    setPrompt({ id: Date.now(), text });
  };
  const { tabLabel, startDemo, demoBar, demoActive } = useStoryDemo(scenario, storyTabs, openStoryTab, ask);
  const [trace, setTrace] = useState<KnowledgeGraphTrace | null>(null);
  const [chatPct, setChatPct] = useState(storedSplit);
  const splitRef = useRef<HTMLDivElement>(null);

  function startResize(e: ReactPointerEvent<HTMLDivElement>) {
    const split = splitRef.current;
    if (!split) return;
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    const rect = split.getBoundingClientRect();
    let pct = chatPct;
    const onMove = (ev: PointerEvent) => {
      pct = Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, ((ev.clientX - rect.left) / rect.width) * 100));
      setChatPct(pct);
    };
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      saveSplit(pct);
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  }

  function nudgeSplit(delta: number | null) {
    const pct = delta === null ? SPLIT_DEFAULT : Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, chatPct + delta));
    setChatPct(pct);
    saveSplit(pct);
  }

  // rag-agent reports the subgraph each answer used as a `knowledge_graph_trace`
  // custom event (see its api.py); switching conversations clears the highlight.
  useEffect(() => {
    setTrace(null);
    if (!graphExtras) return;
    const { unsubscribe } = agent.subscribe({
      onCustomEvent: ({ event }) => {
        if (event.name === "knowledge_graph_trace") setTrace(event.value as KnowledgeGraphTrace);
      },
    });
    return unsubscribe;
  }, [agent, graphExtras]);

  const chat = (
    <div className="panel-card panel-card--chat panel-card--chat-full">
      <ChatPanel
        agent={agent}
        baseUrl={config.ragAgentUrl}
        scenarioSlug={scenario.slug}
        sampleQuestions={scenario.sample_questions}
        accessToken={accessToken}
        variant="full"
        onModel={onModel}
        initialMessages={conversation.initialMessages}
        conversationReady={conversation.ready}
        onRunFinished={() => setSidebarRefreshKey((k) => k + 1)}
        mcpServers={scenario.mcp?.consume}
        prompt={prompt}
      />
    </div>
  );

  return (
    <div className={`workspace workspace--rag${graphExtras ? " workspace--kg" : ""}${demoActive ? " workspace--demo" : ""}`}>
      <ConversationSidebar
        collapsible
        baseUrl={config.ragAgentUrl}
        scenarioSlug={scenario.slug}
        accessToken={accessToken}
        activeConversationId={conversation.conversationId}
        onSelect={conversation.selectConversation}
        onCreate={conversation.onCreate}
        onDeleteActive={conversation.onDeleteActive}
        refreshKey={sidebarRefreshKey}
      />
      <div className="rag-main">
        <div className={`rag-hero${graphExtras ? " rag-hero--compact" : ""}`} hidden={tab === "overview"}>
          <div className="rag-hero-icon">{scenario.icon}</div>
          <div>
            <h2>
              {scenario.title}
              {chatModel && (
                <span className="chat-model-badge">
                  {chatModel.model}
                  {chatModel.provider && ` (${chatModel.provider})`}
                </span>
              )}
            </h2>
            <p title={graphExtras ? scenario.description : undefined}>{scenario.description}</p>
          </div>
        </div>
        {hasTabs && (
          <div className="workspace-tabs">
            {story && (
              <button className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>
                <Icon name="book" /> Overview
              </button>
            )}
            <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
              <Icon name="chat" /> {libraryExtras?.chat_tab_label ?? "Chat"}
            </button>
            {libraryExtras && (
              <button className={tab === "library" ? "active" : ""} onClick={() => setTab("library")}>
                <Icon name="book" /> {libraryExtras.tab_label}
              </button>
            )}
            {mcpTools && (
              <button className={tab === "mcp" ? "active" : ""} onClick={() => setTab("mcp")}>
                <Icon name="external" /> Connect via MCP
              </button>
            )}
          </div>
        )}
        {story && tab === "overview" && <ScenarioOverview scenario={scenario} tabLabel={tabLabel} onOpen={openStoryTab} onAsk={ask} onStartDemo={startDemo} />}
        {demoBar}
        {mcpTools && tab === "mcp" && (
          <Suspense fallback={<div className="app-loading">Loading…</div>}>
            <McpConnectView scenarioSlug={scenario.slug} exposed={mcpTools} accessToken={accessToken} />
          </Suspense>
        )}
        {libraryExtras && tab === "library" && (
          <Suspense fallback={<div className="app-loading">Loading…</div>}>
            <SourceLibraryView scenarioSlug={scenario.slug} extras={libraryExtras} accessToken={accessToken} />
          </Suspense>
        )}
        {hasTabs && !graphExtras ? (
          // Kept mounted: the conversation survives a look at another tab.
          <div className="rag-chat-tab" hidden={tab !== "chat"}>
            {chat}
          </div>
        ) : graphExtras ? (
          // The graph's canvas is only mounted on the chat tab, so the Overview leaves the page quiet.
          tab === "chat" && (
          <div className="rag-split" ref={splitRef} style={{ gridTemplateColumns: `minmax(300px, ${chatPct}fr) 0.75rem minmax(320px, ${100 - chatPct}fr)` }}>
            {chat}
            <div
              className="rag-split-handle"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize the chat and the knowledge graph"
              aria-valuemin={SPLIT_MIN}
              aria-valuemax={SPLIT_MAX}
              aria-valuenow={Math.round(chatPct)}
              tabIndex={0}
              title="Drag to resize · double-click to reset"
              onPointerDown={startResize}
              onDoubleClick={() => nudgeSplit(null)}
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft") nudgeSplit(-4);
                else if (e.key === "ArrowRight") nudgeSplit(4);
              }}
            />
            <Suspense fallback={<div className="kg-panel panel-card">Loading the knowledge graph…</div>}>
              <KnowledgeGraphView scenarioSlug={scenario.slug} extras={graphExtras} accessToken={accessToken} trace={trace} />
            </Suspense>
          </div>
          )
        ) : (
          chat
        )}
      </div>
    </div>
  );
}

function saveSplit(pct: number) {
  try {
    localStorage.setItem(SPLIT_STORAGE_KEY, String(Math.round(pct)));
  } catch {
    // Storage blocked (private window): the split still works for this page.
  }
}
