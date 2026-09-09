import { useEffect, useState } from "react";
import {
  getActiveLlmModel,
  getActiveVoiceSettings,
  getCdcStatus,
  getGatewayRateLimits,
  getLakehouseTables,
  getPipelineJobs,
  getRecentPipelineTriggerEvents,
  getRoadmap,
  getSemanticViews,
  ingestLakehouse,
  listLlmProviders,
  pollCdc,
  runSemanticQuery,
  setActiveLlmModel,
  setActiveVoiceSettings,
  testLlmProvider,
  triggerPipelineJob,
  voiceProviders,
  type Capability,
  type CdcPollResult,
  type CdcStatus,
  type LakehouseTableInfo,
  type LlmProvider,
  type LlmProviderTest,
  type PipelineJobsResult,
  type PipelineTriggerEvent,
  type RateLimit,
  type SemanticQueryResult,
  type SemanticView,
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

function statusBadgeClass(status: Capability["status"]): string {
  if (status === "live") return "settings-badge settings-badge--on";
  if (status === "partial") return "settings-badge settings-badge--partial";
  return "settings-badge settings-badge--off";
}

/**
 * Admin-only view of data-platform-manager: the capability roadmap (what's live/
 * partial/planned in the unified data layer + AI Gateway governance — see that
 * service's core/roadmap.py), pipeline job status/trigger (k3s only — a
 * docker-compose deployment reports why it can't), and llm-gateway's static
 * per-model rate limits.
 */
function DataPlatformSection({ baseUrl, accessToken }: { baseUrl: string; accessToken: string | null }) {
  const [roadmap, setRoadmap] = useState<Capability[] | null>(null);
  const [jobs, setJobs] = useState<PipelineJobsResult | null>(null);
  const [rateLimits, setRateLimits] = useState<RateLimit[] | null>(null);
  const [events, setEvents] = useState<PipelineTriggerEvent[] | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [cdcStatus, setCdcStatus] = useState<CdcStatus | null>(null);
  const [cdcResult, setCdcResult] = useState<CdcPollResult | null>(null);
  const [cdcPolling, setCdcPolling] = useState(false);
  const [lakehouseTables, setLakehouseTables] = useState<string[] | null>(null);
  const [lakehouseResult, setLakehouseResult] = useState<LakehouseTableInfo | null>(null);
  const [lakehouseIngesting, setLakehouseIngesting] = useState(false);
  const [semanticViews, setSemanticViews] = useState<SemanticView[] | null>(null);
  const [semanticResult, setSemanticResult] = useState<SemanticQueryResult | null>(null);
  const [semanticRunning, setSemanticRunning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState<string | null>(null);

  function load() {
    setError(null);
    Promise.all([
      getRoadmap(baseUrl, accessToken),
      getPipelineJobs(baseUrl, accessToken),
      getGatewayRateLimits(baseUrl, accessToken),
    ])
      .then(([r, j, g]) => {
        setRoadmap(r);
        setJobs(j);
        setRateLimits(g);
      })
      .catch((e) => setError((e as Error).message));
  }

  useEffect(load, [baseUrl, accessToken]);

  function loadEvents() {
    setEventsLoading(true);
    getRecentPipelineTriggerEvents(baseUrl, accessToken)
      .then(setEvents)
      .catch((e) => setError((e as Error).message))
      .finally(() => setEventsLoading(false));
  }

  useEffect(loadEvents, [baseUrl, accessToken]);

  function loadCdcStatus() {
    getCdcStatus(baseUrl, accessToken)
      .then(setCdcStatus)
      .catch((e) => setError((e as Error).message));
  }

  useEffect(loadCdcStatus, [baseUrl, accessToken]);

  async function runCdcPoll() {
    setCdcPolling(true);
    try {
      const result = await pollCdc(baseUrl, accessToken);
      setCdcResult(result);
      loadCdcStatus();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCdcPolling(false);
    }
  }

  function loadLakehouseTables() {
    getLakehouseTables(baseUrl, accessToken)
      .then(setLakehouseTables)
      .catch((e) => setError((e as Error).message));
  }

  useEffect(loadLakehouseTables, [baseUrl, accessToken]);

  async function runLakehouseIngest() {
    setLakehouseIngesting(true);
    try {
      const result = await ingestLakehouse(baseUrl, accessToken);
      setLakehouseResult(result);
      loadLakehouseTables();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLakehouseIngesting(false);
    }
  }

  function loadSemanticViews() {
    getSemanticViews(baseUrl, accessToken)
      .then(setSemanticViews)
      .catch((e) => setError((e as Error).message));
  }

  useEffect(loadSemanticViews, [baseUrl, accessToken]);

  async function runSemanticView(viewName: string) {
    setSemanticRunning(viewName);
    try {
      const result = await runSemanticQuery(baseUrl, viewName, accessToken);
      setSemanticResult(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSemanticRunning(null);
    }
  }

  async function trigger(jobName: string) {
    setTriggering(jobName);
    try {
      await triggerPipelineJob(baseUrl, jobName, accessToken);
      // Trigger only returns once the Job is (re)created, not once it's actually
      // running — give the cluster a moment before re-reading status/events.
      setTimeout(load, 1000);
      setTimeout(loadEvents, 1000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setTriggering(null);
    }
  }

  return (
    <>
      <div className="settings-card-header">
        <h3>Data Platform</h3>
      </div>
      <p className="panel-hint">
        What's live in this platform today, partially built, or still planned — plus pipeline job control and AI
        Gateway rate limits.
      </p>
      {error && <p className="error">{error}</p>}
      {!roadmap && !error && <div className="app-loading">Loading…</div>}

      {roadmap && (
        <div className="panel-card settings-card">
          <h3>Capability roadmap</h3>
          <p className="panel-hint">
            Two layers — <strong>Data</strong> (source/generation) and <strong>AI / BI / ML</strong> (consumption
            built on top of it) — plus <strong>Governance</strong>, which applies transversally across both rather
            than sitting alongside them as a third layer.
          </p>
          {(["data", "ai-bi-ml", "governance"] as const).map((pillar) => (
            <div key={pillar} style={{ marginTop: "0.9rem" }}>
              <h4 style={{ margin: "0 0 0.4rem" }}>
                {pillar === "data" ? "Data" : pillar === "ai-bi-ml" ? "AI / BI / ML" : "Governance (transversal)"}
              </h4>
              <div className="settings-grid">
                {roadmap
                  .filter((c) => c.pillar === pillar)
                  .map((c) => (
                    <div key={c.name} className="settings-card-model">
                      <span className={statusBadgeClass(c.status)}>{c.status}</span> <strong>{c.name}</strong>
                      <div className="panel-hint">{c.note}</div>
                    </div>
                  ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {jobs && (
        <div className="panel-card settings-card">
          <h3>Pipeline jobs</h3>
          {!jobs.available ? (
            <p className="panel-hint">{jobs.reason}</p>
          ) : (
            <div className="settings-grid">
              {jobs.jobs.map((job) => (
                <div key={job.name} className="settings-card-model">
                  <strong>{job.name}</strong> — {job.state}
                  <div>
                    <button
                      className="btn-secondary"
                      onClick={() => trigger(job.name)}
                      disabled={triggering === job.name}
                    >
                      {triggering === job.name ? "Triggering…" : "▶ Trigger"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="panel-card settings-card">
        <div className="settings-card-header">
          <h3>Recent events (Kafka)</h3>
          <button className="btn-secondary" onClick={loadEvents} disabled={eventsLoading}>
            {eventsLoading ? "Loading…" : "↻ Refresh"}
          </button>
        </div>
        <p className="panel-hint">
          Pipeline-trigger events read directly off the optional Data Platform profile's event stream — empty if that
          profile isn't running (<code>make data-platform-up</code>), not an error. Re-reads from the start of the
          topic on every refresh, so it's a snapshot for this admin view, not a live tail.
        </p>
        {events && events.length === 0 && <p className="panel-hint">No events yet.</p>}
        {events && events.length > 0 && (
          <div className="settings-grid">
            {events.map((e, i) => (
              <div key={`${e.job}-${e.triggered_at}-${i}`} className="settings-card-model">
                <strong>{e.job}</strong> — {e.triggered_at}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="panel-card settings-card">
        <div className="settings-card-header">
          <h3>Change-Data-Capture</h3>
          <button className="btn-secondary" onClick={runCdcPoll} disabled={cdcPolling}>
            {cdcPolling ? "Polling…" : "▶ Poll now"}
          </button>
        </div>
        <p className="panel-hint">
          Reads real changes off Postgres's own logical replication slot for the document store and forwards each to
          Kafka — a genuine WAL read, not the app re-publishing its own writes (unlike the pipeline-trigger events
          above). On demand, not a background loop — see the roadmap note above.
        </p>
        {cdcStatus && (
          <p className="panel-hint">
            Slot: {cdcStatus.slot_exists ? `active, at ${cdcStatus.confirmed_flush_lsn}` : "not created yet — poll once to create it"}
          </p>
        )}
        {cdcResult && (
          <div className="settings-grid">
            {cdcResult.changes.length === 0 && <p className="panel-hint">No changes since the last poll.</p>}
            {cdcResult.changes.map((c, i) => (
              <div key={`${c.table}-${i}`} className="settings-card-model">
                <strong>
                  {c.operation} {c.table}
                </strong>
                <div className="panel-hint">
                  {Object.entries(c.columns)
                    .slice(0, 3)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(", ")}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="panel-card settings-card">
        <div className="settings-card-header">
          <h3>Lakehouse Table Format</h3>
          <button className="btn-secondary" onClick={runLakehouseIngest} disabled={lakehouseIngesting}>
            {lakehouseIngesting ? "Ingesting…" : "▶ Ingest now"}
          </button>
        </div>
        <p className="panel-hint">
          Snapshots the same demo collection into a real, versioned Apache Iceberg table (Parquet files on the
          object store, cataloged in Postgres) — every ingest appends a new snapshot, so row/snapshot counts grow
          run over run rather than being overwritten.
        </p>
        {lakehouseTables && (
          <p className="panel-hint">
            {lakehouseTables.length === 0 ? "No tables yet — ingest once to create one." : `Tables: ${lakehouseTables.join(", ")}`}
          </p>
        )}
        {lakehouseResult && (
          <div className="settings-grid">
            <div className="settings-card-model">
              <strong>{lakehouseResult.table}</strong>
              <div className="panel-hint">
                {lakehouseResult.rows_ingested !== undefined && `${lakehouseResult.rows_ingested} rows ingested this run, `}
                {lakehouseResult.total_rows} total rows, {lakehouseResult.snapshot_count} snapshots
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="panel-card settings-card">
        <h3>Semantic Modeling & Query Federation</h3>
        <p className="panel-hint">
          A small named catalog of business-friendly queries, each run through an embedded DuckDB engine that
          federates two genuinely separate sources in one SQL statement — the lakehouse's Iceberg table and
          platform-registry's real Postgres tables — with nothing copied into a new store.
        </p>
        {semanticViews && (
          <div className="settings-grid">
            {semanticViews.map((v) => (
              <div key={v.name} className="settings-card-model">
                <strong>{v.name}</strong>
                <div className="panel-hint">{v.description}</div>
                <div>
                  <button
                    className="btn-secondary"
                    onClick={() => runSemanticView(v.name)}
                    disabled={semanticRunning === v.name}
                  >
                    {semanticRunning === v.name ? "Running…" : "▶ Run"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        {semanticResult && (
          <div style={{ marginTop: "0.75rem" }}>
            <strong>{semanticResult.view}</strong>
            {semanticResult.rows.length === 0 ? (
              <p className="panel-hint">No rows returned.</p>
            ) : (
              <div className="table-scroll">
                <table className="data-table" style={{ marginTop: "0.5rem" }}>
                  <thead>
                    <tr>
                      {semanticResult.columns.map((c) => (
                        <th key={c}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {semanticResult.rows.map((row, i) => (
                      <tr key={i}>
                        {semanticResult.columns.map((c) => (
                          <td key={c}>{String(row[c])}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>

      {rateLimits && (
        <div className="panel-card settings-card">
          <h3>AI Gateway rate limits</h3>
          <p className="panel-hint">
            Static per-model ceilings from <code>litellm_config.yaml</code> — not per-tenant budgets (see roadmap
            above for why).
          </p>
          <div className="settings-grid">
            {rateLimits.map((r) => (
              <div key={r.model_name} className="settings-card-model">
                <strong>{r.model_name}</strong> — rpm: {r.rpm ?? "—"}, tpm: {r.tpm ?? "—"}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
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

          <DataPlatformSection baseUrl={config.dataPlatformManagerUrl} accessToken={accessToken} />
        </>
      )}
    </div>
  );
}
