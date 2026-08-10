import { useState } from "react";
import type { ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";
import { DatasetView } from "./DatasetView";
import { MlPredictionsView } from "./MlPredictionsView";
import { ExploreModelView } from "./ExploreModelView";

type Tab = "dataset" | "predict" | "explore";

/**
 * Generic tabular_ml workspace, driven entirely by the scenario's feature_columns/
 * feature_schema (see libs/shared/scenario_schema.py) plus prediction's /predict and
 * /dataset endpoints — no scenario-specific code, so this same component renders
 * churn, mpm, supply_chain, or any future tabular_ml scenario.
 *
 * Three tabs, each a distinct concern (no overlap): Dataset (the data, no model),
 * ML Predictions (running the model — one record or a batch/query), Explore model
 * (understanding the model — global SHAP importance, partial dependence, held-out
 * performance). The assistant chat is a single persistent dock here rather than
 * duplicated per tab, since it's the same scenario-grounded conversation regardless
 * of which tab is open.
 */
export function TabularView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [tab, setTab] = useState<Tab>("dataset");
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMaximized, setChatMaximized] = useState(false);

  return (
    <div className="workspace">
      <div className="workspace-tabs">
        <button className={tab === "dataset" ? "active" : ""} onClick={() => setTab("dataset")}>
          🗂️ Dataset
        </button>
        <button className={tab === "predict" ? "active" : ""} onClick={() => setTab("predict")}>
          🎯 ML Predictions
        </button>
        <button className={tab === "explore" ? "active" : ""} onClick={() => setTab("explore")}>
          🔍 Explore model
        </button>
        <button className="chat-dock-toggle" onClick={() => setChatOpen((o) => !o)}>
          💬 Assistant
        </button>
      </div>

      {tab === "dataset" && <DatasetView scenario={scenario} accessToken={accessToken} />}
      {tab === "predict" && <MlPredictionsView scenario={scenario} accessToken={accessToken} />}
      {tab === "explore" && <ExploreModelView scenario={scenario} accessToken={accessToken} />}

      {chatOpen && (
        <div className="chat-dock-overlay" onClick={() => setChatOpen(false)}>
          <div className={`chat-dock-panel${chatMaximized ? " chat-dock-panel--maximized" : ""}`} onClick={(e) => e.stopPropagation()}>
            <div className="chat-dock-header">
              <span>💬 Ask about {scenario.title}</span>
              <div className="chat-dock-header-actions">
                <button
                  className="chat-dock-maximize"
                  onClick={() => setChatMaximized((m) => !m)}
                  title={chatMaximized ? "Restore" : "Maximize"}
                >
                  {chatMaximized ? "⤡" : "⛶"}
                </button>
                <button className="chat-dock-close" onClick={() => setChatOpen(false)}>
                  ✕
                </button>
              </div>
            </div>
            <ChatPanel baseUrl={config.assistantUrl} scenarioSlug={scenario.slug} sampleQuestions={scenario.sample_questions} accessToken={accessToken} variant="dock" />
          </div>
        </div>
      )}
    </div>
  );
}
