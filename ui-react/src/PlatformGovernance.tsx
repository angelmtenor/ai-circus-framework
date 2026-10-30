import { useEffect, useState } from "react";
import { config } from "./config";
import { downloadGovernanceInventory, getGovernanceOverview, type GovernanceOverview } from "./apiClient";
import { Icon } from "./Icon";

const RISK_LABEL: Record<string, string> = { unassessed: "unassessed" };

function Counts({ title, counts, total }: { title: string; counts: Record<string, number>; total: number }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  return (
    <div className="settings-card-model">
      <strong>{title}</strong>
      {entries.map(([name, n]) => (
        <div key={name} style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.3rem" }}>
          <span style={{ minWidth: "8.5rem" }}>{RISK_LABEL[name] ?? name.replace(/_/g, " ")}</span>
          <span
            aria-hidden
            style={{ height: 8, width: `${Math.max(4, (n / Math.max(total, 1)) * 100)}%`, maxWidth: "60%", background: "var(--accent, #22d3ee)", borderRadius: 4 }}
          />
          <span>{n}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * The "Governance" tab of the admin-only Platform page: which governance switches are on, the AI
 * systems and what their owners *declared* (never inferred), and whether the hash-chained audit
 * trail still verifies. Read-only, content-free, served by platform-registry's
 * `GET /governance/overview` — it works while every feature is off, which is the default. It
 * supports a governance programme; it does not say a deployment is compliant. See docs/governance.
 */
export function PlatformGovernanceTab({ accessToken }: { accessToken: string | null }) {
  const [overview, setOverview] = useState<GovernanceOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const baseUrl = config.platformRegistryUrl;

  function load() {
    setError(null);
    getGovernanceOverview(baseUrl, accessToken)
      .then(setOverview)
      .catch((e) => setError((e as Error).message));
  }

  useEffect(load, [baseUrl, accessToken]);

  async function downloadBom() {
    setDownloading(true);
    try {
      const blob = await downloadGovernanceInventory(baseUrl, accessToken);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "ai-bom.cdx.json";
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDownloading(false);
    }
  }

  const verification = overview?.audit.verification;

  return (
    <>
      <p className="panel-hint">
        Governance at a glance: what is switched on, the AI systems and what their owners declared, and whether the
        audit trail is intact. Every feature is off by default and changes nothing when off. This supports a
        governance programme — it does not make a deployment compliant.
      </p>
      {error && <p className="error">{error}</p>}
      {!overview && !error && <div className="app-loading">Loading…</div>}

      {overview && (
        <>
          <div className="panel-card settings-card">
            <h3>
              <Icon name="shield" size={16} /> Switches
            </h3>
            <div className="settings-grid">
              {overview.switches.map((s) => (
                <div key={s.env} className="settings-card-model">
                  <span className={s.enabled ? "settings-badge settings-badge--on" : "settings-badge settings-badge--off"}>
                    {s.enabled ? "on" : "off"}
                  </span>{" "}
                  <strong>{s.label}</strong>
                  <div className="panel-hint">
                    <code>{s.env}</code>
                    {s.env === "GOVERNANCE_EVENTS_ENABLED" && s.enabled && overview.event_sink ? ` · sink: ${overview.event_sink}` : ""}
                  </div>
                </div>
              ))}
            </div>
            <p className="panel-hint">
              The policy decision point (llm-gateway) and the ML decision records (prediction, dl-inference) are
              configured in those services; see the Capabilities tab for their status.
            </p>
          </div>

          <div className="panel-card settings-card">
            <h3>Admin audit trail</h3>
            {!overview.audit.enabled ? (
              <p className="panel-hint">
                Off. Set <code>GOVERNANCE_AUDIT_ENABLED=true</code> on platform-registry to record every admin change in
                a hash-chained log.
              </p>
            ) : (
              <>
                <p>
                  <span className={verification?.ok ? "settings-badge settings-badge--on" : "settings-badge settings-badge--fail"}>
                    {verification?.ok ? "chain verified" : "chain broken"}
                  </span>{" "}
                  {overview.audit.entries} entr{overview.audit.entries === 1 ? "y" : "ies"} checked
                  {verification && !verification.ok
                    ? ` — first invalid row #${verification.first_invalid_id}${verification.reason ? ` (${verification.reason})` : ""}`
                    : ""}
                </p>
                {overview.audit.recent.length === 0 ? (
                  <p className="panel-hint">No admin changes recorded yet.</p>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>When (UTC)</th>
                        <th>Action</th>
                        <th>Target</th>
                        <th>Tenant</th>
                        <th>Actor</th>
                      </tr>
                    </thead>
                    <tbody>
                      {overview.audit.recent.map((e) => (
                        <tr key={e.id}>
                          <td>{e.id}</td>
                          <td>{e.ts.replace("T", " ").replace(/\.\d+Z$/, "")}</td>
                          <td>{e.action}</td>
                          <td>
                            {e.target_type} · {e.target_id}
                          </td>
                          <td>{e.org_id ?? "—"}</td>
                          <td>{e.actor}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </div>

          <div className="panel-card settings-card">
            <h3>AI-BOM inventory</h3>
            {overview.inventory_path ? (
              <>
                <p className="panel-hint">
                  Every scenario with its declared governance metadata, the gateway's model routes and the trained
                  models, as a CycloneDX 1.6 AI-BOM an external governance tool can read.
                </p>
                <button className="btn-secondary" onClick={downloadBom} disabled={downloading}>
                  {downloading ? "Preparing…" : "Download AI-BOM (JSON)"}
                </button>
              </>
            ) : (
              <p className="panel-hint">
                Off. Set <code>GOVERNANCE_INVENTORY_ENABLED=true</code> on platform-registry to export the AI-BOM.
              </p>
            )}
          </div>
          <div className="panel-card settings-card">
            <h3>AI systems</h3>
            <p className="panel-hint">
              {overview.ai_systems.declared} of {overview.ai_systems.total} scenarios declare a <code>governance:</code>{" "}
              block. Risk and oversight are what the owner declared; <em>unassessed</em> / <em>undeclared</em> mean
              nobody has decided yet.
            </p>
            <div className="settings-grid">
              <Counts title="EU AI Act risk category (declared)" counts={overview.ai_systems.by_risk} total={overview.ai_systems.total} />
              <Counts title="Human oversight (declared)" counts={overview.ai_systems.by_oversight} total={overview.ai_systems.total} />
            </div>
            <table className="data-table" style={{ marginTop: "0.75rem" }}>
              <thead>
                <tr>
                  <th>AI system</th>
                  <th>Kind</th>
                  <th>Declared</th>
                  <th>Owner</th>
                  <th>Risk</th>
                  <th>Oversight</th>
                </tr>
              </thead>
              <tbody>
                {overview.ai_systems.items.map((a) => (
                  <tr key={a.slug}>
                    <td>
                      <strong>{a.title}</strong>
                      <div className="panel-hint">
                        <code>{a.ai_system_id}</code>
                      </div>
                    </td>
                    <td>{a.kind}</td>
                    <td>{a.declared ? "yes" : "no"}</td>
                    <td>{a.owner ?? "—"}</td>
                    <td>{a.risk_category.replace(/_/g, " ")}</td>
                    <td>{a.oversight ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

        </>
      )}
    </>
  );
}
