import { useEffect, useState } from "react";
import { config } from "./config";
import { useIdentity } from "./useIdentity";
import { useTheme } from "./useTheme";
import { listEntitledScenarios, type ScenarioSummary } from "./apiClient";
import { TabularView } from "./TabularView";
import { RagView } from "./RagView";
import { AssistedFormView } from "./AssistedFormView";
import { ScenarioPicker } from "./ScenarioPicker";
import { Settings } from "./Settings";
import { Icon } from "./Icon";
import "./App.css";

// Must match useIdentity.ts's ADMIN_ORG_ID — Settings' LLM Provider section manages
// shared LLM-gateway infrastructure, not a per-tenant entitlement, so it's gated to
// the admin tenant; the Appearance (theme) section is a per-browser preference open
// to every org.
const ADMIN_ORG_ID = "admin";

// EU AI Act Art. 50(1): systems that interact directly with natural persons must
// disclose that clearly, at the latest by the time of first interaction — hence a
// persistent, non-dismissible banner rather than a one-time/cookie-style notice.
function AiDisclosureBanner() {
  return (
    <div className="ai-disclosure-banner" role="status">
      <span className="ai-disclosure-badge">AI</span>
      You are interacting with an AI system. Responses are generated automatically and may be inaccurate.
    </div>
  );
}

function LoginScreen({
  logo,
  logtoError,
  onLogin,
  onLoginWithAdminKey,
  onLoginWithEngineeringDemoKey,
  onLogtoSignOut,
}: {
  logo: string;
  logtoError: string | null;
  onLogin: (orgId: string, roles: string[]) => void;
  onLoginWithAdminKey: (adminKey: string) => Promise<void>;
  onLoginWithEngineeringDemoKey: (demoKey: string) => Promise<void>;
  onLogtoSignOut: () => void;
}) {
  const [orgId, setOrgId] = useState(config.devOrgId);
  const [roles, setRoles] = useState("scenario:churn,scenario:ai_circus_reference");
  const [loginUser, setLoginUser] = useState<"admin" | "engineering-demo">("admin");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loginChecking, setLoginChecking] = useState(false);

  async function submitLogin() {
    setLoginChecking(true);
    setLoginError(null);
    try {
      if (loginUser === "admin") {
        await onLoginWithAdminKey(password);
      } else {
        await onLoginWithEngineeringDemoKey(password);
      }
    } catch (e) {
      setLoginError((e as Error).message);
    } finally {
      setLoginChecking(false);
    }
  }

  return (
    <div className="login-shell">
      <AiDisclosureBanner />
      <div className="login-shell-content">
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

          <div className="login-section">
            <label>
              User
              <select value={loginUser} onChange={(e) => setLoginUser(e.target.value as "admin" | "engineering-demo")}>
                <option value="admin">admin</option>
                <option value="engineering-demo">demo engineering</option>
              </select>
            </label>
            <p className="dev-warning">
              {loginUser === "admin"
                ? "Resolves to the admin tenant, auto-entitled to every scenario."
                : "Resolves to a demo tenant entitled to only the engineering scenarios (predictive maintenance, electric motor, building energy)."}
            </p>
            <label>
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <button className="btn-primary" onClick={submitLogin} disabled={!password || loginChecking}>
              {loginChecking ? "Checking…" : "Log in"}
            </button>
            {loginError && <p className="error">{loginError}</p>}
          </div>

          {!config.devMode && config.logtoAppId && (
            <div className="login-section">
              <button className="btn-primary" onClick={() => onLogin("", [])}>
                Log in with Logto
              </button>
              {logtoError && (
                <>
                  <p className="error">{logtoError}</p>
                  <button className="btn-secondary" onClick={onLogtoSignOut}>
                    Sign out of Logto
                  </button>
                </>
              )}
            </div>
          )}
          {!config.devMode && !config.logtoAppId && (
            <p className="dev-warning">Single sign-on via Logto isn't configured yet — use the login above.</p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const { identity, loading, logtoError, logIn, logInWithAdminKey, logInWithEngineeringDemoKey, logOut } =
    useIdentity();
  const { theme, themes, setThemeId } = useTheme();
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [selected, setSelected] = useState<ScenarioSummary | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [scenariosLoading, setScenariosLoading] = useState(false);
  const [scenariosError, setScenariosError] = useState<string | null>(null);

  useEffect(() => {
    setSelected(null);
    setScenarios([]);
    setShowSettings(false);
    if (!identity) return;
    setScenariosLoading(true);
    setScenariosError(null);
    listEntitledScenarios(config.platformRegistryUrl, identity.orgId, identity.accessToken)
      .then(setScenarios)
      .catch((e) => setScenariosError((e as Error).message))
      .finally(() => setScenariosLoading(false));
  }, [identity]);

  if (loading) return <div className="app-loading">Loading…</div>;
  if (!identity)
    return (
      <LoginScreen
        logo={theme.logo}
        logtoError={logtoError}
        onLogin={logIn}
        onLoginWithAdminKey={logInWithAdminKey}
        onLoginWithEngineeringDemoKey={logInWithEngineeringDemoKey}
        onLogtoSignOut={logOut}
      />
    );

  return (
    <div className="app-shell">
      <div className="app-header-group">
        <AiDisclosureBanner />
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
          <span className="topbar-org">{identity.label}</span>
          <button className="topbar-logout" onClick={logOut}>
            Log out
          </button>
        </header>
      </div>
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
            {!scenariosLoading && selected?.kind === "assisted_form" && (
              <AssistedFormView scenario={selected} accessToken={identity.accessToken} />
            )}
            {!scenariosLoading &&
              selected &&
              !["tabular_ml", "conversational_rag", "assisted_form"].includes(selected.kind) && (
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
