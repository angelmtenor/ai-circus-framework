import { useEffect, useState } from "react";
import { listLlmProviders, testLlmProvider, type LlmProvider, type LlmProviderTest } from "./apiClient";
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
  const [results, setResults] = useState<Record<string, LlmProviderTest>>({});

  function load() {
    setError(null);
    listLlmProviders(config.platformRegistryUrl, accessToken)
      .then(setProviders)
      .catch((e) => setError((e as Error).message));
  }

  useEffect(load, [accessToken]);

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

  return (
    <div className="settings-page">
      <h2>⚙️ LLM Provider Settings</h2>
      <p className="panel-hint">
        Every provider here is configured through <code>.env</code> (this deployment's llm-gateway doesn't run
        litellm's database-backed mode, so runtime key updates from the browser can't apply — see the hint on each
        card for the exact variable names). Use <strong>Test</strong> to see, right now, whether a provider is
        actually reachable and answering.
      </p>
      {error && <p className="error">{error}</p>}
      {!providers && !error && <div className="app-loading">Loading providers…</div>}
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
                <button className="btn-secondary" onClick={() => runTest(p.provider)} disabled={testing === p.provider}>
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
