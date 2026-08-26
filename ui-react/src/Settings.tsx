import { useEffect, useState } from "react";
import {
  getActiveLlmModel,
  getActiveVoiceSettings,
  listLlmProviders,
  setActiveLlmModel,
  setActiveVoiceSettings,
  testLlmProvider,
  voiceProviders,
  type LlmProvider,
  type LlmProviderTest,
  type VoiceProviders,
} from "./apiClient";
import { config } from "./config";
import type { Theme } from "./themes";
import { Icon } from "./Icon";

function AppearanceSection({
  theme,
  themes,
  onThemeChange,
}: {
  theme: Theme;
  themes: Theme[];
  onThemeChange: (id: string) => void;
}) {
  return (
    <div className="panel-card settings-card">
      <h3>Appearance</h3>
      <p className="panel-hint">Colors and logo only — layout stays identical across themes. Saved to this browser.</p>
      <div className="theme-picker">
        {themes.map((t) => (
          <button key={t.id} className={`theme-swatch ${t.id === theme.id ? "active" : ""}`} onClick={() => onThemeChange(t.id)}>
            <span className="theme-swatch-dots">
              {t.categoryPalette.slice(0, 4).map((c) => (
                <span key={c} className="theme-swatch-dot" style={{ background: c }} />
              ))}
            </span>
            {t.label}
          </button>
        ))}
      </div>
    </div>
  );
}

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
/** Keys a Test result / in-flight state by (provider, model) — a provider's models
 * can be tested independently (e.g. GroqCloud's two models can be up/down separately).
 */
function resultKey(provider: string, modelName: string): string {
  return `${provider}::${modelName}`;
}

export function Settings({
  accessToken,
  isAdmin,
  theme,
  themes,
  onThemeChange,
  voiceScenarioSlug,
}: {
  accessToken: string | null;
  isAdmin: boolean;
  theme: Theme;
  themes: Theme[];
  onThemeChange: (id: string) => void;
  // Anchor scenario for agui-voice's per-scenario entitlement check (see
  // api/providers.py) — the data returned isn't scenario-specific, any scenario
  // the admin org is entitled to works. `null` while the scenario list is still
  // loading.
  voiceScenarioSlug: string | null;
}) {
  const [providers, setProviders] = useState<LlmProvider[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testingAll, setTestingAll] = useState(false);
  const [results, setResults] = useState<Record<string, LlmProviderTest>>({});
  const [activeModel, setActiveModel] = useState<string | null>(null);
  const [savingModel, setSavingModel] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  // Which of each provider's models its card currently previews/tests — a provider
  // with only one model never needs this, but it's simplest to always key it by
  // provider and default every provider to its first model.
  const [selectedModel, setSelectedModel] = useState<Record<string, string>>({});

  function load() {
    if (!isAdmin) return;
    setError(null);
    Promise.all([
      listLlmProviders(config.platformRegistryUrl, accessToken),
      getActiveLlmModel(config.platformRegistryUrl, accessToken),
    ])
      .then(([providerList, model]) => {
        setProviders(providerList);
        setActiveModel(model);
        setSelectedModel((prev) => {
          const next = { ...prev };
          for (const p of providerList) {
            // Prefer the currently-active model if it's one of this provider's own,
            // so the card previewing on load matches what's actually in use.
            if (!next[p.provider] || !p.models.some((m) => m.model_name === next[p.provider])) {
              next[p.provider] = p.models.find((m) => m.model_name === model)?.model_name ?? p.models[0]?.model_name ?? "";
            }
          }
          return next;
        });
      })
      .catch((e) => setError((e as Error).message));
  }

  useEffect(load, [accessToken, isAdmin]);

  const [voiceOptions, setVoiceOptions] = useState<VoiceProviders | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [selectedStt, setSelectedStt] = useState("");
  const [selectedTts, setSelectedTts] = useState("");
  const [savingVoice, setSavingVoice] = useState(false);
  const [voiceSaveMessage, setVoiceSaveMessage] = useState<string | null>(null);

  function loadVoice() {
    if (!isAdmin || !voiceScenarioSlug) return;
    setVoiceError(null);
    Promise.all([
      voiceProviders(config.voiceUrl, voiceScenarioSlug, accessToken),
      getActiveVoiceSettings(config.platformRegistryUrl, accessToken),
    ])
      .then(([options, active]) => {
        setVoiceOptions(options);
        // Prefer the persisted choice over the resolved/fallback "active" value
        // above — a picker should show what's actually *saved*, not what agui-voice
        // is falling back to because that choice isn't usable on this instance.
        setSelectedStt(active?.stt_provider ?? options.stt.active);
        setSelectedTts(active?.tts_provider ?? options.tts.active);
      })
      .catch((e) => setVoiceError((e as Error).message));
  }

  useEffect(loadVoice, [accessToken, isAdmin, voiceScenarioSlug]);

  async function saveVoiceSettings() {
    setSavingVoice(true);
    setVoiceSaveMessage(null);
    try {
      const saved = await setActiveVoiceSettings(config.platformRegistryUrl, selectedStt, selectedTts, accessToken);
      setSelectedStt(saved.stt_provider);
      setSelectedTts(saved.tts_provider);
      setVoiceSaveMessage(
        `Now using STT "${saved.stt_provider}" / TTS "${saved.tts_provider}" — applies to the very next voice request, no restart needed.`,
      );
    } catch (e) {
      setVoiceSaveMessage(`Failed to save: ${(e as Error).message}`);
    } finally {
      setSavingVoice(false);
    }
  }

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

  async function runTest(provider: string, modelName: string) {
    const key = resultKey(provider, modelName);
    setTesting(key);
    try {
      const result = await testLlmProvider(config.platformRegistryUrl, provider, modelName, accessToken);
      setResults((r) => ({ ...r, [key]: result }));
    } catch (e) {
      setResults((r) => ({ ...r, [key]: { ok: false, error: (e as Error).message, latency_ms: null } }));
    } finally {
      setTesting(null);
    }
  }

  async function runTestAll() {
    if (!providers) return;
    setTestingAll(true);
    // Fire one request per model (not just per provider — a provider can route more
    // than one, e.g. GroqCloud) and update each card as its own result lands, so the
    // page reflects progress live rather than freezing until the slowest one resolves.
    await Promise.allSettled(
      providers.flatMap((p) =>
        p.models.map(async (m) => {
          const key = resultKey(p.provider, m.model_name);
          try {
            const result = await testLlmProvider(config.platformRegistryUrl, p.provider, m.model_name, accessToken);
            setResults((r) => ({ ...r, [key]: result }));
          } catch (e) {
            setResults((r) => ({ ...r, [key]: { ok: false, error: (e as Error).message, latency_ms: null } }));
          }
        }),
      ),
    );
    setTestingAll(false);
  }

  return (
    <div className="settings-page">
      <h2>
        <Icon name="gear" size={20} /> Settings
      </h2>

      <AppearanceSection theme={theme} themes={themes} onThemeChange={onThemeChange} />

      {isAdmin && (
        <>
          <div className="settings-card-header">
            <h3>LLM Provider Settings</h3>
            {providers && (
              <button className="btn-secondary" onClick={runTestAll} disabled={testingAll || testing !== null}>
                {testingAll ? "Testing all…" : "▶ Test All"}
              </button>
            )}
          </div>
          <p className="panel-hint">Choose which AI provider powers the assistant chat below.</p>
          <details className="settings-card-details">
            <summary>How this works</summary>
            <p className="panel-hint">
              Every provider here is configured through <code>.env</code> (this deployment's llm-gateway doesn't run
              litellm's database-backed mode, so runtime key updates from the browser can't apply — see the hint on
              each card for the exact variable names). Use <strong>Test</strong> (or <strong>Test All</strong> to
              check every provider concurrently) to see, right now, whether a provider is actually reachable and
              answering. At least one provider needs a valid key — or run <code>make ollama-up</code> for a free
              local fallback — for assistant/rag-agent chat to work at all.
            </p>
          </details>
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
                {activeModel && !providers.some((p) => p.models.some((m) => m.model_name === activeModel)) && (
                  <option value={activeModel}>{activeModel} (not in litellm_config.yaml anymore)</option>
                )}
                {providers.map((p) => (
                  <optgroup key={p.provider} label={p.label}>
                    {p.models.map((m) => (
                      <option key={m.model_name} value={m.model_name}>
                        {m.model ?? m.model_name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              {savingModel && <span className="panel-hint"> Saving…</span>}
              {saveMessage && <p className="panel-hint">{saveMessage}</p>}
            </div>
          )}
          {providers && (
            <div className="settings-grid">
              {providers.map((p) => {
                const current = selectedModel[p.provider] ?? p.models[0]?.model_name ?? "";
                const model = p.models.find((m) => m.model_name === current) ?? p.models[0];
                const key = resultKey(p.provider, current);
                const result = results[key];
                return (
                  <div className="panel-card settings-card" key={p.provider}>
                    <div className="settings-card-header">
                      <h3>{p.label}</h3>
                      {model && (
                        <span className={`settings-badge ${model.route_exists ? "settings-badge--on" : "settings-badge--off"}`}>
                          {model.route_exists ? "routed" : "not routed"}
                        </span>
                      )}
                    </div>
                    {p.models.length > 1 ? (
                      <select
                        className="settings-model-select settings-model-select--inline"
                        value={current}
                        onChange={(e) => setSelectedModel((s) => ({ ...s, [p.provider]: e.target.value }))}
                      >
                        {p.models.map((m) => (
                          <option key={m.model_name} value={m.model_name}>
                            {m.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      model && (
                        <div className="settings-card-model">
                          model: <code>{model.model ?? "—"}</code>
                        </div>
                      )
                    )}
                    <button
                      className="btn-secondary"
                      onClick={() => runTest(p.provider, current)}
                      disabled={testing === key || testingAll || !model}
                    >
                      {testing === key ? "Testing…" : "▶ Test"}
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
                    <details className="settings-card-details">
                      <summary>Details</summary>
                      {p.models.length > 1 && model && (
                        <div className="settings-card-model">
                          model: <code>{model.model ?? "—"}</code>
                        </div>
                      )}
                      {model?.api_base && (
                        <div className="settings-card-model">
                          base: <code>{model.api_base}</code>
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
                    </details>
                  </div>
                );
              })}
            </div>
          )}

          <div className="settings-card-header">
            <h3>Voice Mode Settings</h3>
          </div>
          <p className="panel-hint">Choose which speech-to-text/text-to-speech engine powers voice mode.</p>
          <details className="settings-card-details">
            <summary>How this works</summary>
            <p className="panel-hint">
              Which speech-to-text/text-to-speech engine agui-voice uses for live voice mode (MicButton) and the
              speaker icon (SpeakerButton) — applies to the very next request, no restart. Defaults to the
              self-hosted, open-source engines (Whisper/Piper, no API key needed); a cloud option stays disabled
              here until its API key is set in agui-voice's own <code>.env</code>.
            </p>
          </details>
          {voiceError && <p className="error">{voiceError}</p>}
          {!voiceScenarioSlug && !voiceError && <div className="app-loading">Loading…</div>}
          {voiceOptions && (
            <div className="panel-card settings-card">
              <h3>Active STT / TTS</h3>
              <label className="panel-hint" htmlFor="voice-stt-select">
                Speech-to-text
              </label>
              <select
                id="voice-stt-select"
                className="settings-model-select"
                value={selectedStt}
                disabled={savingVoice}
                onChange={(e) => setSelectedStt(e.target.value)}
              >
                {voiceOptions.stt.options.map((o) => (
                  <option key={o.id} value={o.id} disabled={!o.available}>
                    {o.label}
                    {!o.available ? ` — ${o.reason}` : ""}
                  </option>
                ))}
              </select>
              <label className="panel-hint" htmlFor="voice-tts-select">
                Text-to-speech
              </label>
              <select
                id="voice-tts-select"
                className="settings-model-select"
                value={selectedTts}
                disabled={savingVoice}
                onChange={(e) => setSelectedTts(e.target.value)}
              >
                {voiceOptions.tts.options.map((o) => (
                  <option key={o.id} value={o.id} disabled={!o.available}>
                    {o.label}
                    {!o.available ? ` — ${o.reason}` : ""}
                  </option>
                ))}
              </select>
              <button className="btn-secondary" onClick={saveVoiceSettings} disabled={savingVoice}>
                {savingVoice ? "Saving…" : "Save"}
              </button>
              {voiceSaveMessage && <p className="panel-hint">{voiceSaveMessage}</p>}
            </div>
          )}
        </>
      )}
    </div>
  );
}
