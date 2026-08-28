import { useMemo, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import type { ChatModel, ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";
import { ConversationSidebar } from "./ConversationSidebar";
import { DataView } from "./DataView";
import { MlPredictionsView } from "./MlPredictionsView";
import { ExploreModelView } from "./ExploreModelView";
import { Icon } from "./Icon";
import { useChatGenerativeUiActions } from "./chatGenerativeUi";
import { useConversation } from "./useConversation";
import { useScenarioAgent } from "./useScenarioAgent";

type Tab = "data" | "predict" | "explore";

/**
 * Generic tabular_ml workspace, driven entirely by the scenario's feature_columns/
 * feature_schema (see libs/shared/scenario_schema.py) plus prediction's /predict and
 * /dataset endpoints — no scenario-specific code, so this same component renders
 * churn, mpm, supply_chain, or any future tabular_ml scenario.
 *
 * Three tabs, each a distinct concern (no overlap): Data (the data, no model),
 * ML Predictions (running the model — one record or a batch/query), Explore model
 * (understanding the model — global SHAP importance, partial dependence, held-out
 * performance). The assistant chat is a single persistent dock here rather than
 * duplicated per tab, since it's the same scenario-grounded conversation regardless
 * of which tab is open.
 *
 * The whole workspace (not just the chat dock) is wrapped in one <CopilotKit> so
 * MlPredictionsView/ExploreModelView's useCopilotReadable calls share their current
 * on-screen state with the same agent instance the dock chat talks to — "what's the
 * user looking at right now" context, independent of the scenario's static
 * chat.context grounding (see the assistant service's build_system_prompt).
 */
export function TabularView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const conversation = useConversation(config.assistantUrl, scenario.slug, accessToken);
  const agent = useScenarioAgent(config.assistantUrl, scenario.slug, conversation.conversationId, accessToken);
  // See RagView.tsx's identical note: must be memoized, or every re-render of
  // TabularView (tab switches, prediction results, etc.) resets CopilotKit's
  // internal action/context registry — confirmed empirically before this fix.
  const selfManagedAgents = useMemo(() => ({ [scenario.slug]: agent }), [scenario.slug, agent]);

  return (
    <CopilotKit selfManagedAgents={selfManagedAgents}>
      <TabularViewContent scenario={scenario} accessToken={accessToken} agent={agent} conversation={conversation} />
    </CopilotKit>
  );
}

function TabularViewContent({
  scenario,
  accessToken,
  agent,
  conversation,
}: {
  scenario: ScenarioSummary;
  accessToken: string | null;
  agent: ReturnType<typeof useScenarioAgent>;
  conversation: ReturnType<typeof useConversation>;
}) {
  useChatGenerativeUiActions();
  const [tab, setTab] = useState<Tab>("data");
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMaximized, setChatMaximized] = useState(false);
  const [chatModel, setChatModel] = useState<ChatModel | null>(null);
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);

  return (
    <div className="workspace">
      <div className="workspace-tabs">
        <button className={tab === "data" ? "active" : ""} onClick={() => setTab("data")}>
          <Icon name="data" /> Data
        </button>
        <button className={tab === "predict" ? "active" : ""} onClick={() => setTab("predict")}>
          <Icon name="target" /> ML Predictions
        </button>
        <button className={tab === "explore" ? "active" : ""} onClick={() => setTab("explore")}>
          <Icon name="scan" /> Explore model
        </button>
      </div>

      <button className="chat-dock-toggle" onClick={() => setChatOpen((o) => !o)}>
        <Icon name="chat" /> Assistant
      </button>

      {tab === "data" && <DataView scenario={scenario} accessToken={accessToken} />}
      {tab === "predict" && <MlPredictionsView scenario={scenario} accessToken={accessToken} />}
      {tab === "explore" && <ExploreModelView scenario={scenario} accessToken={accessToken} />}

      {chatOpen && (
        <div className="chat-dock-overlay" onClick={() => setChatOpen(false)}>
          <div className={`chat-dock-panel${chatMaximized ? " chat-dock-panel--maximized" : ""}`} onClick={(e) => e.stopPropagation()}>
            <div className="chat-dock-header">
              <span>
                <Icon name="chat" /> Ask about {scenario.title}
                {chatModel && (
                  <span className="chat-model-badge">
                    {chatModel.model}
                    {chatModel.provider && ` (${chatModel.provider})`}
                  </span>
                )}
              </span>
              <div className="chat-dock-header-actions">
                <button
                  className="chat-dock-maximize"
                  onClick={() => setChatMaximized((m) => !m)}
                  title={chatMaximized ? "Restore" : "Maximize"}
                >
                  <Icon name={chatMaximized ? "restore" : "maximize"} size={14} />
                </button>
                <button className="chat-dock-close" onClick={() => setChatOpen(false)}>
                  <Icon name="close" size={14} />
                </button>
              </div>
            </div>
            <div className={`chat-dock-body${chatMaximized ? "" : " chat-dock-body--stacked"}`}>
              <ConversationSidebar
                baseUrl={config.assistantUrl}
                scenarioSlug={scenario.slug}
                accessToken={accessToken}
                activeConversationId={conversation.conversationId}
                onSelect={conversation.selectConversation}
                onCreate={conversation.onCreate}
                onDeleteActive={conversation.onDeleteActive}
                refreshKey={sidebarRefreshKey}
                compact={!chatMaximized}
              />
              <ChatPanel
                agent={agent}
                baseUrl={config.assistantUrl}
                scenarioSlug={scenario.slug}
                sampleQuestions={scenario.sample_questions}
                accessToken={accessToken}
                variant="dock"
                onModel={setChatModel}
                initialMessages={conversation.initialMessages}
                conversationReady={conversation.ready}
                onRunFinished={() => setSidebarRefreshKey((k) => k + 1)}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
