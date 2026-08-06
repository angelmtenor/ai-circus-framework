import { useEffect, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { config } from "./config";
import { useIdentity } from "./useIdentity";
import { listEntitledScenarios, type ScenarioSummary } from "./apiClient";
import { TabularView } from "./TabularView";
import { RagView } from "./RagView";
import { ScenarioPicker } from "./ScenarioPicker";
import { Settings } from "./Settings";
import "./App.css";

// Must match useIdentity.ts's ADMIN_ORG_ID — Settings manages shared LLM-gateway
// infrastructure, not a per-tenant entitlement, so it's gated to the admin tenant.
const ADMIN_ORG_ID = "admin";

function LoginScreen({
  onLogin,
  onLoginWithAdminKey,
}: {
  onLogin: (orgId: string, roles: string[]) => void;
  onLoginWithAdminKey: (adminKey: string) => void;
}) {
  const [orgId, setOrgId] = useState(config.devOrgId);
  const [roles, setRoles] = useState("scenario:churn,scenario:docs_rag");
  const [adminKey, setAdminKey] = useState("");

  return (
    <div className="login-shell">
      <div className="login-card">
        <div className="login-brand">
          <span className="login-brand-icon">🎪</span>
          <h1>ai-circus-framework</h1>
          <p className="login-tagline">Explainable ML &amp; document Q&amp;A, one scenario at a time.</p>
        </div>

        {config.devMode && (
          <div className="login-section">
            <p className="dev-warning">DEV_MODE is on — this bypasses real login. Never enable it beyond local iteration.</p>
            <label>
              Org id
              <input value={orgId} onChange={(e) => setOrgId(e.target.value)} />
            </label>
            <label>
              Roles (comma-separated)
              <input value={roles} onChange={(e) => setRoles(e.target.value)} />
            </label>
            <button className="btn-primary" onClick={() => onLogin(orgId, roles.split(",").map((r) => r.trim()).filter(Boolean))}>
              Log in (dev)
            </button>
          </div>
        )}

        <details className="login-section login-details">
          <summary>Admin key login</summary>
          <p className="dev-warning">Resolves to the admin tenant, auto-entitled to every scenario.</p>
          <label>
            Admin key
            <input type="password" value={adminKey} onChange={(e) => setAdminKey(e.target.value)} />
          </label>
          <button className="btn-primary" onClick={() => onLoginWithAdminKey(adminKey)} disabled={!adminKey}>
            Log in as admin
          </button>
        </details>

        {!config.devMode && (
          <div className="login-section">
            <button className="btn-primary" onClick={() => onLogin("", [])}>
              Log in with Logto
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function AppContent() {
  const { identity, loading, logIn, logInWithAdminKey, logOut } = useIdentity();
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [selected, setSelected] = useState<ScenarioSummary | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [scenariosLoading, setScenariosLoading] = useState(false);
  const [scenariosError, setScenariosError] = useState<string | null>(null);

  useEffect(() => {
    if (!identity) return;
    setScenariosLoading(true);
    setScenariosError(null);
    listEntitledScenarios(config.platformRegistryUrl, identity.orgId)
      .then(setScenarios)
      .catch((e) => setScenariosError((e as Error).message))
      .finally(() => setScenariosLoading(false));
  }, [identity]);

  if (loading) return <div className="app-loading">Loading…</div>;
  if (!identity) return <LoginScreen onLogin={logIn} onLoginWithAdminKey={logInWithAdminKey} />;

  return (
    <div className="app-shell">
      <header className="topbar">
        <button
          className="topbar-brand"
          onClick={() => {
            setSelected(null);
            setShowSettings(false);
          }}
        >
          🎪 ai-circus-framework
        </button>
        {selected && !showSettings && (
          <div className="topbar-scenario">
            <button className="topbar-back" onClick={() => setSelected(null)}>
              ← Scenarios
            </button>
            <span className="topbar-scenario-name">
              {selected.icon} {selected.title}
            </span>
          </div>
        )}
        <div className="topbar-spacer" />
        {identity.orgId === ADMIN_ORG_ID && (
          <button
            className={`topbar-settings ${showSettings ? "active" : ""}`}
            onClick={() => {
              setShowSettings((s) => !s);
              setSelected(null);
            }}
          >
            ⚙️ Settings
          </button>
        )}
        <span className="topbar-org">{identity.orgId}</span>
        <button className="topbar-logout" onClick={logOut}>
          Log out
        </button>
      </header>
      <main className="app-main">
        {showSettings ? (
          <Settings accessToken={identity.accessToken} />
        ) : (
          <>
            {scenariosError && <p className="error">{scenariosError}</p>}
            {scenariosLoading && <div className="app-loading">Loading scenarios…</div>}
            {!scenariosLoading && !scenariosError && !selected && (
              <ScenarioPicker scenarios={scenarios} onSelect={setSelected} />
            )}
            {!scenariosLoading && selected?.kind === "tabular_ml" && (
              <TabularView scenario={selected} accessToken={identity.accessToken} />
            )}
            {!scenariosLoading && selected?.kind === "conversational_rag" && (
              <RagView scenario={selected} accessToken={identity.accessToken} />
            )}
          </>
        )}
      </main>
    </div>
  );
}

export default function App() {
  // CopilotKit provider is the intended integration point for a future AG-UI
  // runtime bridge to rag-agent (see ChatPanel.tsx) — not wired to one yet.
  return (
    <CopilotKit runtimeUrl="/api/copilotkit">
      <AppContent />
    </CopilotKit>
  );
}
