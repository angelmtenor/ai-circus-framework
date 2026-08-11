import { useEffect, useState } from "react";
import { config } from "./config";
import { useIdentity } from "./useIdentity";
import { useTheme } from "./useTheme";
import { listEntitledScenarios, type ScenarioSummary } from "./apiClient";
import { TabularView } from "./TabularView";
import { RagView } from "./RagView";
import { ScenarioPicker } from "./ScenarioPicker";
import { Settings } from "./Settings";
import { Icon } from "./Icon";
import "./App.css";

// Must match useIdentity.ts's ADMIN_ORG_ID — Settings' LLM Provider section manages
// shared LLM-gateway infrastructure, not a per-tenant entitlement, so it's gated to
// the admin tenant; the Appearance (theme) section is a per-browser preference open
// to every org.
const ADMIN_ORG_ID = "admin";

function LoginScreen({
  logo,
  onLogin,
  onLoginWithAdminKey,
  onLoginWithEngineeringDemoKey,
}: {
  logo: string;
  onLogin: (orgId: string, roles: string[]) => void;
  onLoginWithAdminKey: (adminKey: string) => Promise<void>;
  onLoginWithEngineeringDemoKey: (demoKey: string) => Promise<void>;
}) {
  const [orgId, setOrgId] = useState(config.devOrgId);
  const [roles, setRoles] = useState("scenario:churn,scenario:ai_circus_reference");
  const [adminKey, setAdminKey] = useState("");
  const [adminKeyError, setAdminKeyError] = useState<string | null>(null);
  const [adminKeyChecking, setAdminKeyChecking] = useState(false);
  const [engineeringDemoKey, setEngineeringDemoKey] = useState("");
  const [engineeringDemoKeyError, setEngineeringDemoKeyError] = useState<string | null>(null);
  const [engineeringDemoKeyChecking, setEngineeringDemoKeyChecking] = useState(false);

  async function submitAdminKey() {
    setAdminKeyChecking(true);
    setAdminKeyError(null);
    try {
      await onLoginWithAdminKey(adminKey);
    } catch (e) {
      setAdminKeyError((e as Error).message);
    } finally {
      setAdminKeyChecking(false);
    }
  }

  async function submitEngineeringDemoKey() {
    setEngineeringDemoKeyChecking(true);
    setEngineeringDemoKeyError(null);
    try {
      await onLoginWithEngineeringDemoKey(engineeringDemoKey);
    } catch (e) {
      setEngineeringDemoKeyError((e as Error).message);
    } finally {
      setEngineeringDemoKeyChecking(false);
    }
  }

  return (
    <div className="login-shell">
      <div className="login-card">
        <div className="login-brand">
          <img src={logo} alt="AI Circus" className="login-brand-icon" />
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
          <button className="btn-primary" onClick={submitAdminKey} disabled={!adminKey || adminKeyChecking}>
            {adminKeyChecking ? "Checking…" : "Log in as admin"}
          </button>
          {adminKeyError && <p className="error">{adminKeyError}</p>}
        </details>

        <details className="login-section login-details">
          <summary>Engineering demo login</summary>
          <p className="dev-warning">
            Resolves to a demo tenant entitled to only the engineering scenarios (predictive maintenance, electric
            motor, building energy).
          </p>
          <label>
            Engineering demo key
            <input
              type="password"
              value={engineeringDemoKey}
              onChange={(e) => setEngineeringDemoKey(e.target.value)}
            />
          </label>
          <button
            className="btn-primary"
            onClick={submitEngineeringDemoKey}
            disabled={!engineeringDemoKey || engineeringDemoKeyChecking}
          >
            {engineeringDemoKeyChecking ? "Checking…" : "Log in as engineering demo"}
          </button>
          {engineeringDemoKeyError && <p className="error">{engineeringDemoKeyError}</p>}
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

export default function App() {
  const { identity, loading, logIn, logInWithAdminKey, logInWithEngineeringDemoKey, logOut } = useIdentity();
  const { theme, themes, setThemeId } = useTheme();
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
  if (!identity)
    return (
      <LoginScreen
        logo={theme.logo}
        onLogin={logIn}
        onLoginWithAdminKey={logInWithAdminKey}
        onLoginWithEngineeringDemoKey={logInWithEngineeringDemoKey}
      />
    );

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
          <img src={theme.logo} alt="AI Circus" className="topbar-brand-icon" />
        </button>
        {selected && !showSettings && (
          <div className="topbar-scenario">
            <button className="topbar-back" onClick={() => setSelected(null)}>
              <Icon name="back" size={14} /> Scenarios
            </button>
            <span className="topbar-scenario-name">
              {selected.icon} {selected.title}
            </span>
          </div>
        )}
        {showSettings && (
          <div className="topbar-scenario">
            <button className="topbar-back" onClick={() => setShowSettings(false)}>
              <Icon name="back" size={14} /> Scenarios
            </button>
          </div>
        )}
        <div className="topbar-spacer" />
        <button
          className={`topbar-settings ${showSettings ? "active" : ""}`}
          onClick={() => {
            setShowSettings((s) => !s);
            setSelected(null);
          }}
        >
          <Icon name="gear" size={14} /> Settings
        </button>
        <span className="topbar-org">{identity.orgId}</span>
        <button className="topbar-logout" onClick={logOut}>
          Log out
        </button>
      </header>
      <main className="app-main">
        {showSettings ? (
          <Settings
            accessToken={identity.accessToken}
            isAdmin={identity.orgId === ADMIN_ORG_ID}
            theme={theme}
            themes={themes}
            onThemeChange={setThemeId}
          />
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
            {!scenariosLoading && selected && selected.kind !== "tabular_ml" && selected.kind !== "conversational_rag" && (
              <div className="app-loading">
                {selected.title} ({selected.kind}) doesn't have a workspace view yet.
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
