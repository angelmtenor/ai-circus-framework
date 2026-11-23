import { Fragment, lazy, Suspense, useCallback, useMemo, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { modelViews, type ChatModel, type ScenarioSummary, type UiExtras } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";
import { ConversationSidebar } from "./ConversationSidebar";
import { ScenarioOverview } from "./ScenarioOverview";
import { StoryDemoBar } from "./StoryDemoBar";
import { AUDIENCES, useAudience } from "./audience";
import { DataView } from "./DataView";
import { MlPredictionsView } from "./MlPredictionsView";
import { ExploreModelView } from "./ExploreModelView";
import { RegionMapView } from "./RegionMapView";
import { LivePlantView } from "./LivePlantView";
import { ProcessOptimizerView } from "./ProcessOptimizerView";
import { Icon, type IconName } from "./Icon";
import { useChatGenerativeUiActions } from "./chatGenerativeUi";
import { useWorkspaceGuide } from "./guideBridge";
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
// A forecast board whose scenario declares the drought-watch toolset is its control room (docs/adr/0030).
const DroughtControlRoom = lazy(() => import("./DroughtControlRoom").then((m) => ({ default: m.DroughtControlRoom })));
const RecommenderPredictView = lazy(() => import("./RecommenderViews").then((m) => ({ default: m.RecommenderPredictView })));
const RecommenderInsightsView = lazy(() => import("./RecommenderViews").then((m) => ({ default: m.RecommenderInsightsView })));
const FleetHealthView = lazy(() => import("./FleetHealthView").then((m) => ({ default: m.FleetHealthView })));
const RowTriageView = lazy(() => import("./RowTriageView").then((m) => ({ default: m.RowTriageView })));
const MessageScreeningView = lazy(() => import("./MessageScreeningView").then((m) => ({ default: m.MessageScreeningView })));
// The scenario's MCP door on the assistant (docs/adr/0031), when its YAML declares `mcp:`.
const McpConnectView = lazy(() => import("./McpConnectView").then((m) => ({ default: m.McpConnectView })));

// `model:<i>` / `extra:<i>`: the i-th model's tab and its ui_extras tab (see modelViews).
type Tab = "scenario" | "tutorial" | "data" | "mcp" | `model:${number}` | `extra:${number}`;
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
  message_screening: { icon: "mail", label: "Message screening" },
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
/** What a tabular MCP door lets a client ask, from the tools it exposes (docs/adr/0031). */
function mcpAbilities(exposed: string[]): string {
  const parts = ["what it predicts and from which inputs"];
  if (exposed.includes("score_record")) parts.push("the score of one case and what pushed it");
  if (exposed.includes("drought_state")) parts.push("the drought state, forecasts, what-ifs, measures, plan and bulletin");
  return parts.join("; ");
}

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
      return scenario.analysis_toolset === "drought_watch" ? <DroughtControlRoom {...props} /> : <ForecastBoardView {...props} />;
    case "fleet_health":
      return <FleetHealthView {...props} />;
    case "row_triage":
      return <RowTriageView {...props} />;
    case "message_screening":
      return <MessageScreeningView {...props} />;
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
  // A question sent to the chat from outside it (the Overview, the guided demo) — see ChatPanel's `prompt`.
  const [chatPrompt, setChatPrompt] = useState<{ id: number; text: string } | null>(null);
  const [demoIndex, setDemoIndex] = useState<number | null>(null);
  const demo = scenario.story?.demo ?? null;
  // An executive sees the story and the showpiece tabs; the data and model tabs stay one click away for the demo.
  const [audience, setAudience] = useAudience();
  const executive = audience === "executive";

  // A story's tab names (see scenario_schema.StoryConfig) resolved against this workspace.
  const resolveTab = useCallback(
    (name: string): Tab | null => {
      if (name === "overview") return "scenario";
      if (name === "data") return "data";
      if (name === "mcp") return scenario.mcp?.expose.length ? "mcp" : null;
      if (name === "tutorial") return scenario.tutorial ? "tutorial" : null;
      const [part, id] = name.split(":");
      const index = id === undefined ? 0 : views.findIndex((v) => v.slug === `${scenario.slug}~${id}`);
      if (index < 0) return null;
      if (part === "model") return `model:${index}`;
      if (part === "extra" && views[index].ui_extras && EXTRA_TABS[views[index].ui_extras!.kind]) return `extra:${index}`;
      return null;
    },
    [scenario.slug, scenario.tutorial, scenario.mcp, views],
  );
  const tabLabel = useCallback(
    (name: string): string | null => {
      if (name === "assistant") return "Assistant";
      const resolved = resolveTab(name);
      if (resolved === null) return null;
      if (resolved === "scenario") return "Overview";
      if (resolved === "data") return "Data & BI";
      if (resolved === "mcp") return "Connect via MCP";
      if (resolved === "tutorial") return scenario.tutorial?.tab_label ?? "Tutorial";
      const index = Number(resolved.split(":")[1]);
      const view = views[index];
      if (resolved.startsWith("model:")) return index === 0 ? (scenario.main_model_title ?? "ML model") : view.title;
      return view.ui_extras ? (extraTabLabel(view.ui_extras) ?? null) : null;
    },
    [resolveTab, scenario.tutorial, scenario.main_model_title, views],
  );
  function openStoryTab(name: string) {
    if (name === "assistant") {
      setChatOpen(true);
      return;
    }
    const resolved = resolveTab(name);
    if (resolved === null) return;
    if (resolved.startsWith("model:")) setSection("predictions");
    setTab(resolved);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  // Closing the dock drops a question sent from outside, so reopening it never sends that question again.
  function closeChat() {
    setChatOpen(false);
    setChatPrompt(null);
  }
  function ask(question: string) {
    setChatOpen(true);
    setChatPrompt({ id: Date.now(), text: question });
  }
  function goToDemoStep(index: number) {
    if (!demo) return;
    const step = demo.steps[Math.max(0, Math.min(index, demo.steps.length - 1))];
    setDemoIndex(demo.steps.indexOf(step));
    // The chat is an overlay: it opens on an assistant step and gets out of the way elsewhere.
    if (step.tab !== "assistant") closeChat();
    openStoryTab(step.tab);
    if (step.section) setSection(step.section);
  }
  // The tabs this workspace offers the app's guide (guideBridge.ts), by story tab name: every one this scenario has.
  const guideTabMap = useMemo(() => {
    const names = [
      "overview",
      "assistant",
      "tutorial",
      "data",
      "mcp",
      ...views.flatMap((view, index) => {
        const id = index === 0 ? null : view.slug.split("~")[1];
        return id ? [`model:${id}`, `extra:${id}`] : ["model", "extra"];
      }),
    ];
    return Object.fromEntries(
      names.flatMap((name) => {
        const label = tabLabel(name);
        return label ? [[name, label]] : [];
      }),
    );
  }, [views, tabLabel]);
  useWorkspaceGuide(scenario.slug, guideTabMap, openStoryTab, demo ? () => goToDemoStep(0) : undefined, ask);

  return (
    <div className={`workspace${demoIndex !== null ? " workspace--demo" : ""}`}>
      <div className="workspace-tabs">
        <button className={tab === "scenario" ? "active" : ""} onClick={() => setTab("scenario")}>
          <Icon name="book" /> Overview
        </button>
        {scenario.tutorial && (
          <button className={tab === "tutorial" ? "active" : ""} onClick={() => setTab("tutorial")}>
            <Icon name="cap" /> {scenario.tutorial.tab_label}
          </button>
        )}
        {(!executive || tab === "data") && (
          <button className={tab === "data" ? "active" : ""} onClick={() => setTab("data")}>
            <Icon name="data" /> Data & BI
          </button>
        )}
        {views.map((view, index) => {
          const extraTab = view.ui_extras ? EXTRA_TABS[view.ui_extras.kind] : undefined;
          return (
            <Fragment key={view.slug}>
              {(!executive || tab === `model:${index}`) && (
                <button className={tab === `model:${index}` ? "active" : ""} onClick={() => setTab(`model:${index}`)}>
                  <Icon name="target" /> {index === 0 ? (scenario.main_model_title ?? "ML model") : view.title}
                </button>
              )}
              {extraTab && (
                <button className={tab === `extra:${index}` ? "active" : ""} onClick={() => setTab(`extra:${index}`)}>
                  <Icon name={extraTabIcon(view.ui_extras!, extraTab.icon)} />
                  {extraTabLabel(view.ui_extras!)}
                </button>
              )}
            </Fragment>
          );
        })}
        {scenario.mcp?.expose.length ? (
          (!executive || tab === "mcp") && (
            <button className={tab === "mcp" ? "active" : ""} onClick={() => setTab("mcp")}>
              <Icon name="external" /> Connect via MCP
            </button>
          )
        ) : null}
        <div className="audience-switch" role="radiogroup" aria-label="Show this workspace for">
          {AUDIENCES.map((a) => (
            <button
              key={a.id}
              role="radio"
              aria-checked={audience === a.id}
              className={audience === a.id ? "active" : ""}
              title={a.hint}
              onClick={() => {
                setAudience(a.id);
                if (a.id === "executive" && (tab === "data" || tab.startsWith("model:"))) setTab("scenario");
              }}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>

      <button className="chat-dock-toggle" onClick={() => (chatOpen ? closeChat() : setChatOpen(true))}>
        <Icon name="chat" /> Assistant
      </button>

      {tab === "scenario" && (
        <ScenarioOverview
          scenario={scenario}
          audience={audience}
          tabLabel={tabLabel}
          onOpen={openStoryTab}
          onAsk={ask}
          onStartDemo={demo ? () => goToDemoStep(0) : undefined}
        />
      )}
      {demo && demoIndex !== null && (
        <StoryDemoBar
          steps={demo.steps}
          index={demoIndex}
          onIndex={goToDemoStep}
          onExit={() => setDemoIndex(null)}
          onAsk={ask}
          tabLabel={tabLabel}
          besideChat={chatOpen}
        />
      )}
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
        {tab === "mcp" && scenario.mcp?.expose.length ? (
          <McpConnectView
            scenarioSlug={scenario.slug}
            exposed={scenario.mcp.expose}
            accessToken={accessToken}
            baseUrl={config.assistantUrl}
            localPort={8034}
            title="Use this scenario's model from any MCP client"
            owns="model and data"
            emptyHint={
              <>
                A "not trained" error means the model is not trained for your organisation yet: run{" "}
                <code>make k3s-pipeline</code>.
              </>
            }
            lede={
              <p className="mcp-lede">
                LibreChat, Open WebUI, Claude Code, Claude Desktop, VS Code, MCP Inspector or your own agent can ask this
                scenario's model: {mcpAbilities(scenario.mcp.expose)}. Read-only and for your organisation only; the model
                runs here, and your client's model does the reasoning around it.
              </p>
            }
          />
        ) : null}
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
        <div className="chat-dock-overlay" onClick={closeChat}>
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
                <button className="chat-dock-close" onClick={closeChat}>
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
                prompt={chatPrompt}
                locale={scenario.ui_extras && "locale" in scenario.ui_extras ? (scenario.ui_extras.locale ?? "en") : "en"}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
