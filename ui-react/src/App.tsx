import { useEffect, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { config } from "./config";
import { useIdentity } from "./useIdentity";
import { listEntitledScenarios, type ScenarioSummary } from "./apiClient";
import { TabularView } from "./TabularView";
import { ChatPanel } from "./ChatPanel";
import "./App.css";

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
    <div className="login-screen">
      <h1>🎪 ai-circus-framework</h1>

      {config.devMode && (
        <>
          <p className="dev-warning">
            DEV_MODE is on — this bypasses real login. Never enable it beyond local iteration.
          </p>
          <label>
            Org id
            <input value={orgId} onChange={(e) => setOrgId(e.target.value)} />
          </label>
          <label>
            Roles (comma-separated)
            <input value={roles} onChange={(e) => setRoles(e.target.value)} />
          </label>
          <button
            onClick={() => onLogin(orgId, roles.split(",").map((r) => r.trim()).filter(Boolean))}
          >
            Log in (dev)
          </button>
          <hr />
        </>
      )}

      <details>
        <summary>Admin key login</summary>
        <p className="dev-warning">Resolves to the admin tenant, auto-entitled to every scenario.</p>
        <label>
          Admin key
          <input type="password" value={adminKey} onChange={(e) => setAdminKey(e.target.value)} />
        </label>
        <button onClick={() => onLoginWithAdminKey(adminKey)} disabled={!adminKey}>
          Log in as admin
        </button>
      </details>

      {!config.devMode && (
        <>
          <hr />
          <button onClick={() => onLogin("", [])}>Log in</button>
        </>
      )}
    </div>
  );
}

function AppContent() {
  const { identity, loading, logIn, logInWithAdminKey, logOut } = useIdentity();
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [selected, setSelected] = useState<ScenarioSummary | null>(null);

  useEffect(() => {
    if (!identity) return;
    listEntitledScenarios(config.platformRegistryUrl, identity.orgId).then((list) => {
      setScenarios(list);
      setSelected(list[0] ?? null);
    });
  }, [identity]);

  if (loading) return <p>Loading...</p>;
  if (!identity) return <LoginScreen onLogin={logIn} onLoginWithAdminKey={logInWithAdminKey} />;

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <h1>🎪 ai-circus-framework</h1>
        <ul>
          {scenarios.map((s) => (
            <li key={s.slug}>
              <button className={selected?.slug === s.slug ? "active" : ""} onClick={() => setSelected(s)}>
                {s.icon} {s.title}
              </button>
            </li>
          ))}
        </ul>
        <button onClick={logOut}>Log out</button>
      </aside>
      <main className="content">
        {!selected && <p>No scenarios are assigned to your account yet. Contact your admin.</p>}
        {selected?.kind === "tabular_ml" && <TabularView scenario={selected} accessToken={identity.accessToken} />}
        {selected?.kind === "conversational_rag" && (
          <div>
            <h2>
              {selected.icon} {selected.title}
            </h2>
            <p>{selected.description}</p>
            <ChatPanel
              baseUrl={config.ragAgentUrl}
              scenarioSlug={selected.slug}
              sampleQuestions={selected.sample_questions}
              accessToken={identity.accessToken}
            />
          </div>
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
