import { Fragment, lazy, Suspense, useMemo, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { modelViews, type ChatModel, type ScenarioSummary, type UiExtras } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";
import { ConversationSidebar } from "./ConversationSidebar";
import { ScenarioView } from "./ScenarioView";
import { DataView } from "./DataView";
import { MlPredictionsView } from "./MlPredictionsView";
import { ExploreModelView } from "./ExploreModelView";
import { RegionMapView } from "./RegionMapView";
import { LivePlantView } from "./LivePlantView";
import { ProcessOptimizerView } from "./ProcessOptimizerView";
import { Icon, type IconName } from "./Icon";
import { useChatGenerativeUiActions } from "./chatGenerativeUi";
import { useConversation } from "./useConversation";
import { useScenarioAgent } from "./useScenarioAgent";

// Only the scenarios that opt in ever load these — own chunks, so no other workspace
// pays for the ship illustration, the globe/map geometry or the tutorial widgets.
const VoyageView = lazy(() => import("./VoyageView").then((m) => ({ default: m.VoyageView })));
const RiskWatchlistView = lazy(() => import("./RiskWatchlistView").then((m) => ({ default: m.RiskWatchlistView })));
const NetworkTab = lazy(() => import("./NetworkTab").then((m) => ({ default: m.NetworkTab })));
const DispatchTowerView = lazy(() => import("./DispatchTowerView").then((m) => ({ default: m.DispatchTowerView })));
const ShipmentGlobeView = lazy(() => import("./ShipmentGlobeView").then((m) => ({ default: m.ShipmentGlobeView })));
const MoneyTrailView = lazy(() => import("./MoneyTrailView").then((m) => ({ default: m.MoneyTrailView })));
const CaseDeskView = lazy(() => import("./CaseDeskView").then((m) => ({ default: m.CaseDeskView })));
const TutorialView = lazy(() => import("./TutorialView").then((m) => ({ default: m.TutorialView })));
// A forecast scenario's Predictions/Insights/board tabs (docs/adr/0016-time-series-forecasting.md).
const ForecastPredictView = lazy(() => import("./ForecastViews").then((m) => ({ default: m.ForecastPredictView })));
const ForecastInsightsView = lazy(() => import("./ForecastViews").then((m) => ({ default: m.ForecastInsightsView })));
const ForecastBoardView = lazy(() => import("./ForecastViews").then((m) => ({ default: m.ForecastBoardView })));
const RecommenderPredictView = lazy(() => import("./RecommenderViews").then((m) => ({ default: m.RecommenderPredictView })));
const RecommenderInsightsView = lazy(() => import("./RecommenderViews").then((m) => ({ default: m.RecommenderInsightsView })));
const FleetHealthView = lazy(() => import("./FleetHealthView").then((m) => ({ default: m.FleetHealthView })));
const RowTriageView = lazy(() => import("./RowTriageView").then((m) => ({ default: m.RowTriageView })));

// `model:<i>` / `extra:<i>`: the i-th model's tab and its ui_extras tab (see modelViews).
type Tab = "scenario" | "tutorial" | "data" | `model:${number}` | `extra:${number}`;
type ModelSection = "predictions" | "insights";

// Tab chrome per ui_extras kind — the renderer itself is picked below. Partial: the
// deep_learning-only kinds (triage_board, reading_room) never reach a tabular scenario
// (scenario_schema.py rejects them there) and are rendered by DeepLearningView instead.
const EXTRA_TABS: Partial<Record<UiExtras["kind"], { icon: IconName; label: string }>> = {
  region_map: { icon: "map", label: "Regional Map" },
  live_plant: { icon: "factory", label: "Live Plant" },
  process_optimizer: { icon: "sparkle", label: "Optimizer" },
  voyage_explorer: { icon: "ship", label: "Voyage" },
  risk_watchlist: { icon: "shield", label: "Watchlist" },
  network_explorer: { icon: "network", label: "Network" },
  dispatch_tower: { icon: "radar", label: "Dispatch Tower" },
  shipment_globe: { icon: "globe", label: "Globe" },
  money_trail: { icon: "coins", label: "Money Trail" },
  case_desk: { icon: "desk", label: "Case desk" },
  forecast_board: { icon: "pulse", label: "Forecast board" },
  fleet_health: { icon: "gauge", label: "Fleet health" },
  row_triage: { icon: "scan", label: "Triage" },
};

function extraTabLabel(extras: UiExtras): string | undefined {
  if ("tab_label" in extras) return extras.tab_label;
  return EXTRA_TABS[extras.kind]?.label;
}

function extraTabIcon(extras: UiExtras, fallback: IconName): IconName {
  // The voyage engine draws more than ships (see scenes.ts).
  return extras.kind === "voyage_explorer" && extras.scene === "office_tower" ? "building" : fallback;
}

/**
 * Generic tabular_ml workspace, driven entirely by the scenario's feature_columns/
 * feature_schema (see libs/shared/scenario_schema.py) plus prediction's /predict and
 * /dataset endpoints — no scenario-specific code, so this same component renders
 * churn, mpm, supply_chain, or any future tabular_ml scenario.
 *
 * Three tabs, each a distinct concern (no overlap): Scenario (what/why — description,
 * data source, feature glossary), Data & BI (the data itself, no model — query
 * builder plus a chart dashboard) and the model, with a switch between Predictions
 * (running it — one record or a batch/query) and Insights (understanding it — global
 * SHAP importance, partial dependence, held-out performance). A scenario with several
 * models (docs/adr/0018) has one such tab per model. The assistant chat is a single
 * persistent dock here rather than duplicated per tab, since it's the same
 * scenario-grounded conversation regardless of which tab is open.
 *
 * One more tab per model is opt-in via `scenario.ui_extras` (see
 * libs/shared/scenario_schema.py's UiExtras) — still no scenario-specific UI code:
 * RegionMapView/LivePlantView/ProcessOptimizerView are three generic renderers any
 * tabular_ml scenario can reuse by setting the matching YAML block, the same way
 * `form`/`feature_schema` already drive generic renderers instead of per-scenario
 * components.
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

/** One model: running it and understanding it, a switch apart. */
function ModelTab({
  scenario,
  accessToken,
  section,
  onSection,
}: {
  scenario: ScenarioSummary;
  accessToken: string | null;
  section: ModelSection;
  onSection: (section: ModelSection) => void;
}) {
  const forecast = scenario.task_type === "forecast";
  const recommender = scenario.task_type === "recommendation";
  const rows = !forecast && !recommender;
  return (
    <>
      <div className="model-section-switch" role="group" aria-label="Model section">
        <button className={section === "predictions" ? "active" : ""} onClick={() => onSection("predictions")}>
          <Icon name="target" /> Predictions
        </button>
        <button className={section === "insights" ? "active" : ""} onClick={() => onSection("insights")}>
          <Icon name="scan" /> Insights
        </button>
      </div>
      {section === "predictions" && rows && <MlPredictionsView scenario={scenario} accessToken={accessToken} />}
      {section === "insights" && rows && <ExploreModelView scenario={scenario} accessToken={accessToken} />}
      {section === "predictions" && recommender && <RecommenderPredictView scenario={scenario} accessToken={accessToken} />}
      {section === "insights" && recommender && <RecommenderInsightsView scenario={scenario} accessToken={accessToken} />}
      {section === "predictions" && forecast && <ForecastPredictView scenario={scenario} accessToken={accessToken} />}
      {section === "insights" && forecast && <ForecastInsightsView scenario={scenario} accessToken={accessToken} />}
    </>
  );
}

/** A model's optional `ui_extras` tab. */
function ExtraTab({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const props = { scenario, accessToken };
  switch (scenario.ui_extras?.kind) {
    case "region_map":
      return <RegionMapView {...props} />;
    case "live_plant":
      return <LivePlantView {...props} />;
    case "process_optimizer":
      return <ProcessOptimizerView {...props} />;
    case "voyage_explorer":
      return <VoyageView {...props} />;
    case "risk_watchlist":
      return <RiskWatchlistView {...props} />;
    case "network_explorer":
      return <NetworkTab {...props} />;
    case "dispatch_tower":
      return <DispatchTowerView {...props} />;
    case "shipment_globe":
      return <ShipmentGlobeView {...props} />;
    case "money_trail":
      return <MoneyTrailView {...props} />;
    case "case_desk":
      return <CaseDeskView {...props} />;
    case "forecast_board":
      return <ForecastBoardView {...props} />;
    case "fleet_health":
      return <FleetHealthView {...props} />;
    case "row_triage":
      return <RowTriageView {...props} />;
    default:
      return null;
  }
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
  // A tutorial scenario opens on its tutorial — that *is* the scenario's front page.
  const [tab, setTab] = useState<Tab>(scenario.tutorial ? "tutorial" : "scenario");
  const views = useMemo(() => modelViews(scenario), [scenario]);
  const [section, setSection] = useState<ModelSection>("predictions");
  const [dataModel, setDataModel] = useState(0);
  const hasMainExtra = Boolean(scenario.ui_extras && EXTRA_TABS[scenario.ui_extras.kind]);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMaximized, setChatMaximized] = useState(false);
  const [chatModel, setChatModel] = useState<ChatModel | null>(null);
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);

  return (
    <div className="workspace">
      <div className="workspace-tabs">
        <button className={tab === "scenario" ? "active" : ""} onClick={() => setTab("scenario")}>
          <Icon name="book" /> Scenario
        </button>
        {scenario.tutorial && (
          <button className={tab === "tutorial" ? "active" : ""} onClick={() => setTab("tutorial")}>
            <Icon name="cap" /> {scenario.tutorial.tab_label}
          </button>
        )}
        <button className={tab === "data" ? "active" : ""} onClick={() => setTab("data")}>
          <Icon name="data" /> Data & BI
        </button>
        {views.map((view, index) => {
          const extraTab = view.ui_extras ? EXTRA_TABS[view.ui_extras.kind] : undefined;
          return (
            <Fragment key={view.slug}>
              <button className={tab === `model:${index}` ? "active" : ""} onClick={() => setTab(`model:${index}`)}>
                <Icon name="target" /> {index === 0 ? (scenario.main_model_title ?? "ML model") : view.title}
              </button>
              {extraTab && (
                <button className={tab === `extra:${index}` ? "active" : ""} onClick={() => setTab(`extra:${index}`)}>
                  <Icon name={extraTabIcon(view.ui_extras!, extraTab.icon)} />
                  {extraTabLabel(view.ui_extras!)}
                </button>
              )}
            </Fragment>
          );
        })}
      </div>

      <button className="chat-dock-toggle" onClick={() => setChatOpen((o) => !o)}>
        <Icon name="chat" /> Assistant
      </button>

      {tab === "scenario" && <ScenarioView scenario={scenario} />}
      {tab === "data" && (
        <>
          {views.length > 1 && (
            <div className="model-section-switch" role="group" aria-label="Dataset">
              {views.map((view, index) => (
                <button key={view.slug} className={dataModel === index ? "active" : ""} onClick={() => setDataModel(index)}>
                  {index === 0 ? (scenario.main_model_title ?? "ML model") : view.title}
                </button>
              ))}
            </div>
          )}
          <DataView key={views[dataModel].slug} scenario={views[dataModel]} accessToken={accessToken} />
        </>
      )}
      <Suspense fallback={<div className="app-loading">Loading…</div>}>
        {tab === "tutorial" && scenario.tutorial && (
          <TutorialView scenario={scenario} accessToken={accessToken} onOpenExtra={hasMainExtra ? () => setTab("extra:0") : undefined} />
        )}
        {views.map((view, index) => (
          <Fragment key={view.slug}>
            {tab === `model:${index}` && (
              <ModelTab scenario={view} accessToken={accessToken} section={section} onSection={setSection} />
            )}
            {tab === `extra:${index}` && <ExtraTab scenario={view} accessToken={accessToken} />}
          </Fragment>
        ))}
      </Suspense>

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
                documentTool={scenario.document_tool}
                locale={scenario.ui_extras && "locale" in scenario.ui_extras ? (scenario.ui_extras.locale ?? "en") : "en"}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
