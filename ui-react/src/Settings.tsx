import { useEffect, useState } from "react";
import {
  getActiveLlmModel,
  listLlmProviders,
  setActiveLlmModel,
  testLlmProvider,
  type LlmProvider,
  type LlmProviderTest,
} from "./apiClient";
import { config } from "./config";

/**
 * Admin-only LLM provider status/test page. There's no "save a key from the browser"
 * here on purpose: litellm's own runtime model-management API (the only way to apply
 * a key without a restart) requires its DB-backed proxy mode, which this deployment
 * doesn't run (see services/llm-gateway/litellm_config.yaml and
 * services/platform-registry/src/platform_registry/core/llm_settings.py for why) — so
 * every provider here is configured via `.env` + a gateway restart, same as every
 * other secret in this repo. What IS real: live status from llm-gateway itself, and a
 * genuine round-trip completion call per provider via "Test".
 */
export function Settings({ accessToken }: { accessToken: string | null }) {
  const [providers, setProviders] = useState<LlmProvider[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testingAll, setTestingAll] = useState(false);
  const [results, setResults] = useState<Record<string, LlmProviderTest>>({});
  const [activeModel, setActiveModel] = useState<string | null>(null);
  const [savingModel, setSavingModel] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  function load() {
    setError(null);
    Promise.all([
      listLlmProviders(config.platformRegistryUrl, accessToken),
      getActiveLlmModel(config.platformRegistryUrl, accessToken),
    ])
      .then(([providerList, model]) => {
        setProviders(providerList);
        setActiveModel(model);
      })
      .catch((e) => setError((e as Error).message));
  }

  useEffect(load, [accessToken]);

  async function saveActiveModel(modelName: string) {
    setSavingModel(true);
    setSaveMessage(null);
    try {
      const saved = await setActiveLlmModel(config.platformRegistryUrl, modelName, accessToken);
      setActiveModel(saved);
      setSaveMessage(`Now using "${saved}" — applies to the very next chat request, no restart needed.`);
    } catch (e) {
      setSaveMessage(`Failed to save: ${(e as Error).message}`);
    } finally {
      setSavingModel(false);
    }
  }

  async function runTest(provider: string) {
    setTesting(provider);
    try {
      const result = await testLlmProvider(config.platformRegistryUrl, provider, accessToken);
      setResults((r) => ({ ...r, [provider]: result }));
    } catch (e) {
      setResults((r) => ({ ...r, [provider]: { ok: false, error: (e as Error).message, latency_ms: null } }));
    } finally {
      setTesting(null);
    }
  }

  async function runTestAll() {
    if (!providers) return;
    setTestingAll(true);
    // Fire one request per provider (instead of the batched test-all endpoint) and
    // update each card as its own result lands, so the page reflects progress live
    // rather than freezing until the slowest provider's round-trip finally resolves.
    await Promise.allSettled(
      providers.map(async (p) => {
        try {
          const result = await testLlmProvider(config.platformRegistryUrl, p.provider, accessToken);
          setResults((r) => ({ ...r, [p.provider]: result }));
        } catch (e) {
          setResults((r) => ({ ...r, [p.provider]: { ok: false, error: (e as Error).message, latency_ms: null } }));
        }
      }),
    );
    setTestingAll(false);
  }

  return (
    <div className="settings-page">
      <div className="settings-card-header">
        <h2>⚙️ LLM Provider Settings</h2>
        {providers && (
          <button className="btn-secondary" onClick={runTestAll} disabled={testingAll || testing !== null}>
            {testingAll ? "Testing all…" : "▶ Test All"}
          </button>
        )}
      </div>
      <p className="panel-hint">
        Every provider here is configured through <code>.env</code> (this deployment's llm-gateway doesn't run
        litellm's database-backed mode, so runtime key updates from the browser can't apply — see the hint on each
        card for the exact variable names). Use <strong>Test</strong> (or <strong>Test All</strong> to check every
        provider concurrently) to see, right now, whether a provider is actually reachable and answering. At least
        one provider needs a valid key — or run <code>make ollama-up</code> for a free local fallback — for
        assistant/rag-agent chat to work at all.
      </p>
      {error && <p className="error">{error}</p>}
      {!providers && !error && <div className="app-loading">Loading providers…</div>}
      {providers && (
        <div className="panel-card settings-card">
          <h3>Active model</h3>
          <p className="panel-hint">
            Which model <code>assistant</code>/<code>rag-agent</code> use for their next chat request — applies
            immediately, no restart. Only choosing among providers already routed in{" "}
            <code>litellm_config.yaml</code>; entering a brand-new provider/key still requires editing{" "}
            <code>.env</code> (see the cards below).
          </p>
          <select
            className="settings-model-select"
            value={activeModel ?? ""}
            disabled={savingModel}
            onChange={(e) => saveActiveModel(e.target.value)}
          >
            {activeModel && !providers.some((p) => p.model_name === activeModel) && (
              <option value={activeModel}>{activeModel} (not in litellm_config.yaml anymore)</option>
            )}
            {providers.map((p) => (
              <option key={p.provider} value={p.model_name}>
                {p.label} — {p.model_name}
              </option>
            ))}
          </select>
          {savingModel && <span className="panel-hint"> Saving…</span>}
          {saveMessage && <p className="panel-hint">{saveMessage}</p>}
        </div>
      )}
      {providers && (
        <div className="settings-grid">
          {providers.map((p) => {
            const result = results[p.provider];
            return (
              <div className="panel-card settings-card" key={p.provider}>
                <div className="settings-card-header">
                  <h3>{p.label}</h3>
                  <span className={`settings-badge ${p.route_exists ? "settings-badge--on" : "settings-badge--off"}`}>
                    {p.route_exists ? "routed" : "not routed"}
                  </span>
                </div>
                <div className="settings-card-model">
                  model: <code>{p.model ?? "—"}</code>
                </div>
                {p.api_base && (
                  <div className="settings-card-model">
                    base: <code>{p.api_base}</code>
                  </div>
                )}
                <p className="panel-hint">{p.hint}</p>
                <div className="settings-card-envvars">
                  {p.env_vars.map((v) => (
                    <code key={v} className="settings-envvar">
                      {v}
                    </code>
                  ))}
                </div>
                <button
                  className="btn-secondary"
                  onClick={() => runTest(p.provider)}
                  disabled={testing === p.provider || testingAll}
                >
                  {testing === p.provider ? "Testing…" : "▶ Test"}
                </button>
                {result && (
                  <div className={`settings-result ${result.ok ? "settings-result--ok" : "settings-result--fail"}`}>
                    {result.ok ? (
                      <>
                        ✅ Working ({result.latency_ms}ms) — replied "{result.reply}"
                      </>
                    ) : (
                      <>❌ {result.error}</>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
