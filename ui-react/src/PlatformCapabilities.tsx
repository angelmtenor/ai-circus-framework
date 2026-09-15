import { useEffect, useState } from "react";
import {
  getCdcStatus,
  getGatewayRateLimits,
  getLakehouseTables,
  getPipelineJobs,
  getRecentPipelineTriggerEvents,
  getRoadmap,
  getSemanticViews,
  ingestLakehouse,
  pollCdc,
  runSemanticQuery,
  triggerPipelineJob,
  type Capability,
  type CdcPollResult,
  type CdcStatus,
  type LakehouseTableInfo,
  type PipelineJobsResult,
  type PipelineTriggerEvent,
  type RateLimit,
  type SemanticQueryResult,
  type SemanticView,
} from "./apiClient";

function statusBadgeClass(status: Capability["status"]): string {
  if (status === "live") return "settings-badge settings-badge--on";
  if (status === "partial") return "settings-badge settings-badge--partial";
  return "settings-badge settings-badge--off";
}

/**
 * The "Capabilities" tab of the admin-only Platform page (PlatformStatus.tsx):
 * data-platform-manager's capability roadmap (what's live/partial/planned in the
 * unified data layer + AI Gateway governance — see that service's core/roadmap.py),
 * pipeline job status/trigger (k3s only — a docker-compose deployment reports why it
 * can't), recent Kafka trigger events, CDC polling, Lakehouse ingestion, semantic-view
 * queries and llm-gateway's static per-model rate limits. None of this is a
 * preference, it's operations — which is why it sits next to the health dashboard
 * rather than in Settings (Appearance / LLM provider / Voice).
 */
export function PlatformCapabilitiesTab({ baseUrl, accessToken }: { baseUrl: string; accessToken: string | null }) {
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
      <p className="panel-hint">
        What's live in this platform today, partially built, or still planned — plus pipeline job control, event
        streaming, change-data-capture, Lakehouse, semantic queries and AI Gateway rate limits.
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
