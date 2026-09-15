import { useCallback, useEffect, useState } from "react";
import { getPlatformStatus, type ComponentGroup, type ComponentStatus, type PlatformStatus } from "./apiClient";
import { PlatformCapabilitiesTab } from "./PlatformCapabilities";
import { Icon } from "./Icon";

/**
 * Admin-only "Platform" page — everything operational, in two tabs, so Settings can stay
 * purely about preferences/configuration (Appearance, LLM provider, Voice):
 *
 * - **Health**: is every microservice / infra piece / monitor up and healthy, with
 *   one-click access to the admin consoles (Langfuse for GenAI traces, MLflow for ML
 *   training runs, Keycloak, SeaweedFS). Everything shown comes from data-platform-manager's
 *   admin-gated GET /platform/status (see its core/platform_status.py): that service probes
 *   each component over the cluster network — the browser can't reach internal-only
 *   services like llm-gateway, Postgres or ClickHouse itself — and, on k8s, adds each pod's
 *   ready/restart counts. This component just renders and polls; the target list, grouping
 *   and console links live server-side so adding a component never needs UI code (same
 *   principle as scenario-driven views).
 * - **Capabilities**: what the platform can do today vs. what's planned (data-platform-manager's
 *   roadmap) plus the live operational controls behind it (pipeline jobs, Kafka events, CDC,
 *   Lakehouse, semantic queries, gateway rate limits) — see PlatformCapabilities.tsx.
 *
 * Only the visible tab is mounted, so the health poll stops while the Capabilities tab is
 * open and its many one-shot requests don't fire until it's actually looked at.
 */

const POLL_SECONDS = 15;

type PlatformTab = "health" | "capabilities";

const GROUP_LABELS: Record<ComponentGroup, { title: string; hint: string }> = {
  services: { title: "Microservices", hint: "The platform's own services — every scenario kind is served by these." },
  infra: { title: "Infrastructure", hint: "Stores and identity every service depends on." },
  observability: {
    title: "Observability",
    hint: "GenAI monitor (Langfuse: every LLM call, per tenant/scenario) and MLOps monitor (MLflow: every training run).",
  },
};
const GROUP_ORDER: ComponentGroup[] = ["services", "infra", "observability"];

function badgeClass(status: ComponentStatus["status"]): string {
  if (status === "up") return "settings-badge settings-badge--on";
  if (status === "degraded") return "settings-badge settings-badge--partial";
  if (status === "down") return "settings-badge settings-badge--fail";
  return "settings-badge settings-badge--off";
}

function badgeLabel(status: ComponentStatus["status"]): string {
  return status === "not_deployed" ? "not deployed" : status;
}

function formatAge(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds < 90) return `${seconds}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function summarize(components: ComponentStatus[]): { up: number; degraded: number; down: number; total: number } {
  const deployed = components.filter((c) => c.status !== "not_deployed");
  return {
    up: deployed.filter((c) => c.status === "up").length,
    degraded: deployed.filter((c) => c.status === "degraded").length,
    down: deployed.filter((c) => c.status === "down").length,
    total: deployed.length,
  };
}

function ComponentCard({ component }: { component: ComponentStatus }) {
  const { pod } = component;
  return (
    <div className={`panel-card settings-card platform-card platform-card--${component.status}`}>
      <div className="settings-card-header">
        <h3>{component.name}</h3>
        <span className={badgeClass(component.status)}>{badgeLabel(component.status)}</span>
      </div>
      <p className="settings-card-model">{component.description}</p>
      <div className="platform-card-meta">
        <span title="Probe result">{component.detail}</span>
        {component.latency_ms !== null && <span title="Probe latency">{component.latency_ms} ms</span>}
        {pod && (
          <span title={`Pod phase: ${pod.phase}`}>
            {pod.ready ? "ready" : "not ready"}
            {pod.restarts > 0 && ` · ${pod.restarts} restart${pod.restarts === 1 ? "" : "s"}`}
            {pod.age_seconds !== null && ` · up ${formatAge(pod.age_seconds)}`}
          </span>
        )}
      </div>
      {component.console_url && (
        <a className="btn-secondary platform-card-link" href={component.console_url} target="_blank" rel="noreferrer">
          Open console <Icon name="external" size={12} />
        </a>
      )}
    </div>
  );
}

export function PlatformStatusView({ baseUrl, accessToken }: { baseUrl: string; accessToken: string | null }) {
  const [tab, setTab] = useState<PlatformTab>("health");

  return (
    <div className="settings-page platform-page">
      <h2>
        <Icon name="pulse" size={20} /> Platform
      </h2>
      <div className="workspace-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "health"} className={tab === "health" ? "active" : ""} onClick={() => setTab("health")}>
          <Icon name="pulse" size={14} /> Health
        </button>
        <button
          role="tab"
          aria-selected={tab === "capabilities"}
          className={tab === "capabilities" ? "active" : ""}
          onClick={() => setTab("capabilities")}
        >
          <Icon name="data" size={14} /> Capabilities
        </button>
      </div>
      {tab === "health" ? (
        <HealthTab baseUrl={baseUrl} accessToken={accessToken} />
      ) : (
        <PlatformCapabilitiesTab baseUrl={baseUrl} accessToken={accessToken} />
      )}
    </div>
  );
}

function HealthTab({ baseUrl, accessToken }: { baseUrl: string; accessToken: string | null }) {
  const [status, setStatus] = useState<PlatformStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(() => {
    setRefreshing(true);
    getPlatformStatus(baseUrl, accessToken)
      .then((s) => {
        setStatus(s);
        setError(null);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setRefreshing(false));
  }, [baseUrl, accessToken]);

  // Immediate load + a steady poll while the page is open (same inline-setInterval
  // shape as LivePlantView's tick) — cleared on unmount so nothing keeps probing.
  useEffect(() => {
    load();
    const id = setInterval(load, POLL_SECONDS * 1000);
    return () => clearInterval(id);
  }, [load]);

  const totals = status ? summarize(status.components) : null;
  const consoles = status?.components.filter((c) => c.console_url) ?? [];

  return (
    <>
      <div className="settings-card-header">
        <p className="panel-hint">
          Live health of every microservice, store and monitor, re-checked every {POLL_SECONDS}s
          {status?.in_cluster ? " — with pod readiness/restarts from the Kubernetes API." : "."}
          {status && ` Last check: ${new Date(status.checked_at).toLocaleTimeString()}.`}
        </p>
        <button className="btn-secondary" onClick={load} disabled={refreshing}>
          {refreshing ? "Checking…" : "↻ Refresh"}
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {!status && !error && <div className="app-loading">Checking the platform…</div>}

      {status && totals && (
        <>
          <div className="platform-summary">
            <span className="settings-badge settings-badge--on">{totals.up} up</span>
            {totals.degraded > 0 && (
              <span className="settings-badge settings-badge--partial">{totals.degraded} degraded</span>
            )}
            {totals.down > 0 && <span className="settings-badge settings-badge--fail">{totals.down} down</span>}
            <span className="settings-card-model">
              {totals.total} components deployed
              {status.components.length > totals.total && ` (${status.components.length - totals.total} optional, off)`}
            </span>
          </div>

          {consoles.length > 0 && (
            <div className="platform-consoles">
              {consoles.map((c) => (
                <a
                  key={c.name}
                  className="btn-secondary"
                  href={c.console_url ?? undefined}
                  target="_blank"
                  rel="noreferrer"
                  title={c.description}
                >
                  {c.name === "langfuse" && "GenAI monitor · "}
                  {c.name === "mlflow" && "MLOps monitor · "}
                  {c.name} <Icon name="external" size={12} />
                </a>
              ))}
            </div>
          )}

          {GROUP_ORDER.map((group) => {
            const items = status.components.filter((c) => c.group === group);
            if (items.length === 0) return null;
            return (
              <section key={group} className="platform-group">
                <h3>{GROUP_LABELS[group].title}</h3>
                <p className="panel-hint">{GROUP_LABELS[group].hint}</p>
                <div className="settings-grid">
                  {items.map((c) => (
                    <ComponentCard key={c.name} component={c} />
                  ))}
                </div>
              </section>
            );
          })}
        </>
      )}
    </>
  );
}
