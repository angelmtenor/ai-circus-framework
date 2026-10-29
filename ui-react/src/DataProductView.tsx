import { CopilotKit } from "@copilotkit/react-core";
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import type { ChatModel, ScenarioSummary } from "./apiClient";
import { ChatPanel } from "./ChatPanel";
import { ConversationSidebar } from "./ConversationSidebar";
import { config } from "./config";
import { dataClient, DataApiError, formatMetric, optionLabel, type DataClient, type Overview, type ViewAs } from "./dataApi";
import { Icon, type IconName } from "./Icon";
import { domainLabel } from "./ScenarioPicker";
import { useChatGenerativeUiActions } from "./chatGenerativeUi";
import { useConversation } from "./useConversation";
import { useScenarioAgent } from "./useScenarioAgent";
import "./dataProduct.css";

const DataTourView = lazy(() => import("./DataTourView").then((m) => ({ default: m.DataTourView })));
const DataFlowView = lazy(() => import("./DataFlowView").then((m) => ({ default: m.DataFlowView })));
const DataDashboardView = lazy(() => import("./DataDashboardView").then((m) => ({ default: m.DataDashboardView })));
const DataExploreView = lazy(() => import("./DataExploreView").then((m) => ({ default: m.DataExploreView })));
const DataQualityView = lazy(() => import("./DataQualityView").then((m) => ({ default: m.DataQualityView })));
const InsightsView = lazy(() => import("./InsightsView").then((m) => ({ default: m.InsightsView })));
const StorefrontView = lazy(() => import("./StorefrontView").then((m) => ({ default: m.StorefrontView })));

type Tab = "tour" | "overview" | "flow" | "dashboards" | "explore" | "quality" | "shop" | "insights";

const TABS: { key: Tab; icon: IconName; label: string }[] = [
  { key: "tour", icon: "cap", label: "Guided tour" },
  // Only when the data product declares apps.insights (its control room).
  { key: "insights", icon: "wave", label: "Insights" },
  { key: "overview", icon: "book", label: "Data product" },
  { key: "flow", icon: "pulse", label: "Live flow" },
  { key: "dashboards", icon: "gauge", label: "Dashboards" },
  { key: "explore", icon: "data", label: "Explore" },
  { key: "quality", icon: "shield", label: "Quality & lineage" },
  // Only when the data product declares apps.storefront (a fictional web shop on its data).
  { key: "shop", icon: "cart", label: "Web shop" },
];

export type DataProductProps = {
  scenario: ScenarioSummary;
  client: DataClient;
  overview: Overview | null;
  refresh: () => void;
  // Workspace-level moves a tab may make (the guided tour drives the others).
  navigate?: (tab: Exclude<Tab, "tour" | "overview">) => void;
  accessToken?: string | null;
  onViewAs?: (viewAs: ViewAs) => void;
  openChat?: () => void;
};

/**
 * Generic `data_product` workspace — one renderer for every data product, driven by the
 * scenario's `data_product` block and data-api (the same "one renderer per kind" rule as
 * TabularView / DeepLearningView). Tabs: a guided tour of the platform (DataTourView — the
 * front page, a tutorial at three depths), the data product itself (sources, tiers, access),
 * the live flow of records through the platform with the simulator, governed dashboards,
 * exploration (records, datasets, SQL, quarantine) and quality & lineage. The admin can
 * "view as" any of the data product's roles to show the access rules everywhere at once —
 * the assistant included.
 */
export function DataProductView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const conversation = useConversation(config.assistantUrl, scenario.slug, accessToken);
  const agent = useScenarioAgent(config.assistantUrl, scenario.slug, conversation.conversationId, accessToken);
  const selfManagedAgents = useMemo(() => ({ [scenario.slug]: agent }), [scenario.slug, agent]);
  return (
    <CopilotKit selfManagedAgents={selfManagedAgents}>
      <DataProductContent scenario={scenario} accessToken={accessToken} agent={agent} conversation={conversation} />
    </CopilotKit>
  );
}

function DataProductContent({
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
  const [tab, setTab] = useState<Tab>("tour");
  const [viewAs, setViewAs] = useState<ViewAs>(null);
  const client = useMemo(() => dataClient(scenario.slug, accessToken, viewAs), [scenario.slug, accessToken, viewAs]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMaximized, setChatMaximized] = useState(false);
  const [chatModel, setChatModel] = useState<ChatModel | null>(null);
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      client
        .overview()
        .then((o) => !cancelled && (setOverview(o), setError(null)))
        .catch((e: DataApiError) => !cancelled && setError(e.message));
    load();
    const id = window.setInterval(load, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [client, tick]);

  const props: DataProductProps = {
    scenario,
    client,
    overview,
    refresh,
    navigate: setTab,
    accessToken,
    onViewAs: setViewAs,
    openChat: () => setChatOpen(true),
  };
  return (
    <div className="workspace data-product">
      <div className="workspace-tabs">
        {TABS.filter(
          (t) => (t.key !== "shop" || scenario.data_product?.apps?.storefront) && (t.key !== "insights" || scenario.data_product?.apps?.insights),
        ).map((t) => (
          <button key={t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
            <Icon name={t.icon} /> {t.key === "insights" ? (scenario.data_product?.apps?.insights?.title ?? t.label) : t.label}
          </button>
        ))}
      </div>
      <button className="chat-dock-toggle" onClick={() => setChatOpen((o) => !o)}>
        <Icon name="chat" /> Assistant
      </button>

      <AccessBar overview={overview} scenario={scenario} viewAs={viewAs} onViewAs={setViewAs} />
      {error && (
        <div className="dp-error">
          <Icon name="info" /> {error}
        </div>
      )}

      <Suspense fallback={<div className="app-loading">Loading…</div>}>
        {tab === "tour" && <DataTourView {...props} />}
        {tab === "overview" && <DataProductOverview {...props} />}
        {tab === "flow" && <DataFlowView {...props} />}
        {tab === "dashboards" && <DataDashboardView {...props} />}
        {tab === "explore" && <DataExploreView {...props} />}
        {tab === "quality" && <DataQualityView {...props} />}
        {tab === "shop" && scenario.data_product?.apps?.storefront && <StorefrontView {...props} />}
        {tab === "insights" && scenario.data_product?.apps?.insights && <InsightsView {...props} />}
      </Suspense>

      {chatOpen && (
        <div className="chat-dock-overlay" onClick={() => setChatOpen(false)}>
          <div className={`chat-dock-panel${chatMaximized ? " chat-dock-panel--maximized" : ""}`} onClick={(e) => e.stopPropagation()}>
            <div className="chat-dock-header">
              <span>
                <Icon name="chat" /> Ask the data of {scenario.title}
                {viewAs && <span className="dp-role-pill">as {overview?.access.role_label ?? viewAs.role}</span>}
                {chatModel && (
                  <span className="chat-model-badge">
                    {chatModel.model}
                    {chatModel.provider && ` (${chatModel.provider})`}
                  </span>
                )}
              </span>
              <div className="chat-dock-header-actions">
                <button className="chat-dock-maximize" onClick={() => setChatMaximized((m) => !m)} title={chatMaximized ? "Restore" : "Maximize"}>
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
                // The assistant's data tools call data-api with the same "view as".
                forwardedProps={viewAs ? { data_view_as: viewAs } : undefined}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** Who am I to this data product — and, for the platform admin, a "view as" switch. */
function AccessBar({
  overview,
  scenario,
  viewAs,
  onViewAs,
}: {
  overview: Overview | null;
  scenario: ScenarioSummary;
  viewAs: ViewAs;
  onViewAs: (v: ViewAs) => void;
}) {
  const dp = scenario.data_product;
  const [values, setValues] = useState("S001");
  if (!overview || !dp) return null;
  const access = overview.access;
  const canSwitch = access.role === dp.access.admin_role || viewAs !== null;
  const filterColumn = dp.access.row_filter?.column;
  const options = overview.row_filter_options ?? [];
  return (
    <div className="dp-access-bar">
      <span className="dp-access-who">
        <Icon name="shield" size={14} /> <b>{access.role_label}</b>
        {access.row_filter && (
          <span className="dp-role-pill">
            {access.row_filter.column} ∈{" "}
            {access.row_filter.values.map((v) => optionLabel(v, overview.row_filter_options)).join(", ") || "∅"}
          </span>
        )}
        {access.hidden.length > 0 && <span className="dp-role-pill dp-role-pill--muted">hidden: {access.hidden.join(", ")}</span>}
        <span className="dp-role-pill dp-role-pill--muted">layers: {access.layers.join(" · ")}</span>
        {access.fallback_data && <span className="dp-role-pill dp-role-pill--muted">shared demo data ({access.org_id})</span>}
      </span>
      {canSwitch && (
        <span className="dp-view-as">
          <label>
            View as{" "}
            <select
              value={viewAs?.role ?? ""}
              onChange={(e) => {
                const role = e.target.value;
                if (!role) return onViewAs(null);
                const filtered = dp.access.roles[role]?.rows === "filtered";
                onViewAs({ role, values: filtered ? values.split(",").map((v) => v.trim()).filter(Boolean) : [] });
              }}
            >
              <option value="">myself</option>
              {overview.roles
                .filter((r) => r.name !== dp.access.admin_role)
                .map((r) => (
                  <option key={r.name} value={r.name}>
                    {r.label}
                  </option>
                ))}
            </select>
          </label>
          {viewAs && dp.access.roles[viewAs.role]?.rows === "filtered" && filterColumn && options.length > 0 && (
            <select
              className="dp-view-as-values"
              value={viewAs.values[0] ?? ""}
              title={`The ${filterColumn} this role sees`}
              onChange={(e) => {
                setValues(e.target.value);
                onViewAs({ ...viewAs, values: [e.target.value] });
              }}
            >
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {optionLabel(o.value, options)}
                </option>
              ))}
            </select>
          )}
          {viewAs && dp.access.roles[viewAs.role]?.rows === "filtered" && filterColumn && options.length === 0 && (
            <input
              className="dp-view-as-values"
              value={values}
              title={`${filterColumn} values this role sees`}
              onChange={(e) => setValues(e.target.value)}
              onBlur={() => onViewAs({ ...viewAs, values: values.split(",").map((v) => v.trim()).filter(Boolean) })}
            />
          )}
        </span>
      )}
    </div>
  );
}

const TIERS: { key: string; label: string; what: string; url?: string; tool?: string }[] = [
  { key: "core", label: "Core", what: "Kafka · CDC · contracts · bronze · Iceberg lake · dbt · data-api" },
  { key: "analytics", label: "Analytics", what: "ClickHouse marts · hot path · Dagster orchestration", url: config.dagsterUrl, tool: "Dagster" },
  { key: "bi", label: "BI", what: "Apache Superset dashboards as code", url: config.supersetUrl, tool: "Superset" },
  { key: "governance", label: "Governance", what: "OpenMetadata catalogue · lineage · quality", url: config.openMetadataUrl, tool: "OpenMetadata" },
];

const SOURCE_ICON: Record<string, IconName> = { kafka: "pulse", files: "download", cdc: "data" };

function DataProductOverview({ scenario, overview }: DataProductProps) {
  const dp = scenario.data_product;
  if (!dp) return null;
  const hot = overview?.hot;
  const fmt = (m: string) => formatMetric(Number(hot?.today?.[m] ?? 0), m.endsWith("_cents") ? "currency_cents" : "integer");
  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>
          {scenario.icon} {scenario.title}
        </h3>
        <p style={{ marginTop: "-0.2rem" }}>{scenario.description}</p>
        <div className="scenario-meta-row">
          <span className="scenario-meta-pill">Domain: {domainLabel(scenario.industry)}</span>
          <span className="scenario-meta-pill">Data product · {dp.sources.length} sources</span>
          <span className="scenario-meta-pill">
            {dp.datasets.length} curated datasets · {dp.metrics.length} governed metrics
          </span>
          <span className="scenario-meta-pill">Marts on {overview?.marts_engine ?? "…"}</span>
        </div>
        {hot && (
          <div className="dp-kpis">
            {hot.measures.map((m) => (
              <div className="kpi-tile" key={m}>
                <div className="kpi-label">{m.replace(/_cents$/, "").replace(/_/g, " ")} today</div>
                <div className="kpi-value">{fmt(m)}</div>
                <div className="kpi-sub">live · approximate</div>
              </div>
            ))}
            <div className="kpi-tile">
              <div className="kpi-label">curated through</div>
              <div className="kpi-value dp-small">
                {overview?.transform?.curated_through ? new Date(overview.transform.curated_through).toLocaleTimeString() : "—"}
              </div>
              <div className="kpi-sub">{overview?.transform ? `last dbt run: ${overview.transform.status}` : "transform not run yet"}</div>
            </div>
          </div>
        )}
      </div>

      <div className="panel-card">
        <h4>Platform tiers</h4>
        <p className="panel-hint">One cluster, four switchable tiers (docs/data-platform). A tier that is off just shows as off.</p>
        <div className="dp-tiers">
          {TIERS.map((t) => {
            const up = overview?.tiers?.[t.key] ?? false;
            return (
              <div key={t.key} className={`dp-tier${up ? " dp-tier--up" : ""}`}>
                <div className="dp-tier-head">
                  <span className={`dp-dot${up ? " dp-dot--up" : ""}`} /> {t.label}
                </div>
                <div className="dp-tier-what">{t.what}</div>
                {t.url && up && (
                  <a href={t.url} target="_blank" rel="noreferrer" className="dp-tier-link">
                    Open {t.tool} <Icon name="external" size={12} />
                  </a>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="panel-card">
        <h4>Sources</h4>
        <div className="dp-sources">
          {dp.sources.map((s) => (
            <div key={s.id} className="dp-source">
              <div className="dp-source-head">
                <Icon name={SOURCE_ICON[s.type] ?? "data"} /> {s.label}
                <span className="dp-role-pill dp-role-pill--muted">{s.type}</span>
              </div>
              {s.topic && <code>{s.topic}</code>}
              {s.prefix && (
                <code>
                  landing/{scenario.slug}/{s.prefix} → {s.lands_in}
                </code>
              )}
              {s.tables && <div className="dp-source-tables">{s.tables.map((t) => t.label).join(" · ")}</div>}
              {s.rules && s.rules.length > 0 && (
                <ul className="dp-rules">
                  {s.rules.map((r) => (
                    <li key={r.name}>{r.description}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="panel-card">
        <h4>Who sees what</h4>
        <table className="dp-table">
          <thead>
            <tr>
              <th>Role</th>
              <th>Rows</th>
              <th>Layers</th>
              <th>Hidden fields</th>
              <th>SQL</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(dp.access.roles).map(([name, role]) => (
              <tr key={name} className={overview?.access.role === name ? "dp-row--active" : ""}>
                <td>
                  <b>{role.label}</b> <code>{name}</code>
                </td>
                <td>{role.rows === "filtered" ? `own ${dp.access.row_filter?.column} only` : "all"}</td>
                <td>{role.layers.join(", ")}</td>
                <td>{role.hidden.join(", ") || "—"}</td>
                <td>{role.sql ? "yes" : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="panel-hint">
          Tenancy is never a role: every query is scoped to your organisation first. Loyalty ids are pseudonymised (HMAC) in silver.
        </p>
      </div>
    </div>
  );
}
