import { useEffect, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { config } from "./config";
import { useIdentity } from "./useIdentity";
import { listEntitledScenarios, type ScenarioSummary } from "./apiClient";
import { ChurnView } from "./ChurnView";
import { ChatPanel } from "./ChatPanel";
import "./App.css";

function LoginScreen({ onLogin }: { onLogin: (orgId: string, roles: string[]) => void }) {
  const [orgId, setOrgId] = useState(config.devOrgId);
  const [roles, setRoles] = useState("scenario:churn,scenario:docs_rag");

  if (!config.devMode) {
    return (
      <div className="login-screen">
        <h1>🎪 ai-circus-framework</h1>
        <button onClick={() => onLogin("", [])}>Log in</button>
      </div>
    );
  }

  return (
    <div className="login-screen">
      <h1>🎪 ai-circus-framework</h1>
      <p className="dev-warning">DEV_MODE is on — this bypasses real login. Never enable it beyond local iteration.</p>
      <label>
        Org id
        <input value={orgId} onChange={(e) => setOrgId(e.target.value)} />
      </label>
      <label>
        Roles (comma-separated)
        <input value={roles} onChange={(e) => setRoles(e.target.value)} />
      </label>
      <button onClick={() => onLogin(orgId, roles.split(",").map((r) => r.trim()).filter(Boolean))}>
        Log in (dev)
      </button>
    </div>
  );
}

function AppContent() {
  const { identity, loading, logIn, logOut } = useIdentity();
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
  if (!identity) return <LoginScreen onLogin={logIn} />;

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
        {selected?.kind === "tabular_ml" && <ChurnView accessToken={identity.accessToken} />}
        {selected?.kind === "conversational_rag" && (
          <div>
            <h2>💬 Ask Your Documents</h2>
            <ChatPanel baseUrl={config.ragAgentUrl} accessToken={identity.accessToken} />
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
