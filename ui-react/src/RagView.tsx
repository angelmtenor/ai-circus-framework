import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import type { ChatModel, KnowledgeGraphTrace, ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";
import { ConversationSidebar } from "./ConversationSidebar";
import { useChatGenerativeUiActions } from "./chatGenerativeUi";
import { useConversation } from "./useConversation";
import { useScenarioAgent } from "./useScenarioAgent";

// Canvas + d3-force: loaded only for a scenario that ships a knowledge graph.
const KnowledgeGraphView = lazy(() => import("./KnowledgeGraphView"));

/**
 * conversational_rag workspace — a full-page ChatGPT-style window grounded in the
 * scenario's vectorized documents. rag-agent exposes no document-listing endpoint
 * (see api.py), so there is no real "browse the uploaded documents" list to show
 * here — the chat itself (with cited sources per reply) is the exploration surface.
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
  const [trace, setTrace] = useState<KnowledgeGraphTrace | null>(null);

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
      />
    </div>
  );

  return (
    <div className="workspace workspace--rag">
      <ConversationSidebar
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
        <div className="rag-hero">
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
            <p>{scenario.description}</p>
          </div>
        </div>
        {graphExtras ? (
          <div className="rag-split">
            {chat}
            <Suspense fallback={<div className="kg-panel panel-card">Loading the knowledge graph…</div>}>
              <KnowledgeGraphView scenarioSlug={scenario.slug} extras={graphExtras} accessToken={accessToken} trace={trace} />
            </Suspense>
          </div>
        ) : (
          chat
        )}
      </div>
    </div>
  );
}
