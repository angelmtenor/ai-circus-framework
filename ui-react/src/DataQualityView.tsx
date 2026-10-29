import { useEffect, useMemo, useState } from "react";
import { config } from "./config";
import type { DatasetInfo, LineageGraph, TransformRun } from "./dataApi";
import type { DataProductProps } from "./DataProductView";
import { Icon } from "./Icon";

type Run = TransformRun & { models_failed: number; failures: { node: string; status: string; message: string }[] };
const LAYERS = ["source", "bronze", "silver", "gold", "serve"] as const;
const LAYER_TITLE: Record<string, string> = {
  source: "Sources",
  bronze: "Bronze (raw)",
  silver: "Silver (conformed)",
  gold: "Gold (marts)",
  serve: "Governed metrics",
};

/**
 * Is the data trustworthy, and where does it come from? Freshness of every dataset against
 * its SLO, the dbt runs (models and tests, with failures), file deliveries reconciled
 * against their control totals, and the lineage graph from sources to metrics. Links to the
 * analytics/governance tools when their tiers are up.
 */
export function DataQualityView({ client, overview }: DataProductProps) {
  const access = overview?.access;
  const [datasets, setDatasets] = useState<DatasetInfo[] | null>(null);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [deliveries, setDeliveries] = useState<Record<string, unknown>[] | null>(null);
  const [lineage, setLineage] = useState<LineageGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hasOps = !!access?.layers.includes("ops");

  useEffect(() => {
    client.datasets().then(setDatasets).catch((e: Error) => setError(e.message));
    client.lineage().then(setLineage).catch((e: Error) => setError(e.message));
    if (hasOps) {
      client.transformRuns().then(setRuns).catch(() => setRuns([]));
      client
        .datasetRows("ops_file_deliveries", 100)
        .then((r) => setDeliveries(r.rows))
        .catch(() => setDeliveries([]));
    }
  }, [client, hasOps]);

  const gaps = (deliveries ?? []).filter((d) => d.status !== "reconciled");
  return (
    <div className="tab-panel">
      {error && <p className="dp-error">{error}</p>}
      <div className="dp-quality-grid">
        <div className="panel-card">
          <h4>
            <Icon name="gauge" /> Freshness
          </h4>
          <table className="dp-table">
            <tbody>
              {datasets
                ?.filter((d) => d.layer !== "ops")
                .map((d) => (
                  <tr key={d.name}>
                    <td>
                      <span className={`dp-layer dp-layer--${d.layer}`}>{d.layer}</span> {d.label}
                    </td>
                    <td>{d.rows !== undefined ? `${d.rows.toLocaleString()} rows` : d.restricted ? "restricted" : "—"}</td>
                    <td>
                      {d.freshness ? (
                        <span className={`dp-fresh${d.freshness.ok ? "" : " dp-fresh--late"}`}>
                          {d.freshness.age_minutes} min / SLO {d.freshness_minutes}
                        </span>
                      ) : (
                        <span className="panel-hint">{d.built ? "no SLO" : "not built"}</span>
                      )}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        <div className="panel-card">
          <h4>
            <Icon name="shield" /> Checks
          </h4>
          <div className="dp-checks">
            <div className="kpi-tile">
              <div className="kpi-label">dbt tests (last run)</div>
              <div className="kpi-value">
                {overview?.transform ? `${overview.transform.tests_passed}/${overview.transform.tests_passed + overview.transform.tests_failed}` : "—"}
              </div>
              <div className="kpi-sub">{overview?.transform?.message ?? "no run yet"}</div>
            </div>
            <div className="kpi-tile">
              <div className="kpi-label">quarantine (open)</div>
              <div className="kpi-value">{overview?.ops.quarantine?.open ?? "—"}</div>
              <div className="kpi-sub">{overview?.ops.quarantine ? `${overview.ops.quarantine.today} today` : "data stewards only"}</div>
            </div>
            <div className="kpi-tile">
              <div className="kpi-label">file deliveries</div>
              <div className="kpi-value">{deliveries ? `${deliveries.length - gaps.length}/${deliveries.length}` : "—"}</div>
              <div className="kpi-sub">{deliveries ? `${gaps.length} not reconciled` : "ops layer only"}</div>
            </div>
          </div>
          <div className="dp-tool-links">
            {overview?.tiers?.analytics && (
              <a className="dp-tier-link" href={config.dagsterUrl} target="_blank" rel="noreferrer">
                Dagster runs & asset checks <Icon name="external" size={12} />
              </a>
            )}
            {overview?.tiers?.governance && (
              <a className="dp-tier-link" href={config.openMetadataUrl} target="_blank" rel="noreferrer">
                OpenMetadata catalogue & lineage <Icon name="external" size={12} />
              </a>
            )}
          </div>
        </div>
      </div>

      <div className="panel-card">
        <h4>
          <Icon name="network" /> Lineage
        </h4>
        <p className="panel-hint">
          From the scenario's sources and the dbt manifest of the last run{lineage?.generated_at ? ` (${new Date(lineage.generated_at).toLocaleString()})` : ""}. Hover a node
          to trace it upstream and downstream.
        </p>
        {lineage && <LineageDag graph={lineage} />}
      </div>

      {hasOps && (
        <div className="dp-quality-grid">
          <div className="panel-card">
            <h4>Transform runs</h4>
            <table className="dp-table">
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Target</th>
                  <th>Status</th>
                  <th>Models</th>
                  <th>Tests</th>
                  <th>Watermark</th>
                </tr>
              </thead>
              <tbody>
                {runs?.map((r) => (
                  <tr key={r.run_id} title={r.failures.map((f) => `${f.node}: ${f.message}`).join("\n")}>
                    <td>{new Date(r.started_at).toLocaleTimeString()}</td>
                    <td>{r.target}</td>
                    <td>
                      <span className={`dp-status dp-status--${r.status}`}>{r.status}</span>
                    </td>
                    <td>
                      {r.models_ok}
                      {r.models_failed ? ` (+${r.models_failed} failed)` : ""}
                    </td>
                    <td>
                      {r.tests_passed}
                      {r.tests_failed ? ` / ${r.tests_failed} failed` : ""}
                    </td>
                    <td>{r.watermark ? new Date(r.watermark).toLocaleTimeString() : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="panel-card">
            <h4>File deliveries vs control files</h4>
            <div className="dp-table-scroll dp-table-scroll--short">
              <table className="dp-table">
                <thead>
                  <tr>
                    <th>Delivery</th>
                    <th>Expected</th>
                    <th>Loaded</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {[...gaps, ...(deliveries ?? []).filter((d) => d.status === "reconciled")].slice(0, 60).map((d) => (
                    <tr key={String(d.delivery)}>
                      <td>
                        <code>{String(d.delivery)}</code>
                      </td>
                      <td>{String(d.expected_count ?? "—")}</td>
                      <td>{String(d.loaded_count ?? "—")}</td>
                      <td>
                        <span className={`dp-status dp-status--${d.status === "reconciled" ? "success" : "failed"}`}>{String(d.status)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const NODE_W = 168;
const NODE_H = 26;
const COL_GAP = 70;
const ROW_GAP = 8;

function LineageDag({ graph }: { graph: LineageGraph }) {
  const [hover, setHover] = useState<string | null>(null);
  const layout = useMemo(() => {
    const columns = LAYERS.map((layer) => graph.nodes.filter((n) => n.layer === layer).sort((a, b) => a.label.localeCompare(b.label)));
    const height = Math.max(...columns.map((c) => c.length)) * (NODE_H + ROW_GAP) + 40;
    const pos = new Map<string, { x: number; y: number }>();
    columns.forEach((col, ci) => {
      const offset = (height - 40 - col.length * (NODE_H + ROW_GAP)) / 2;
      col.forEach((n, ri) => pos.set(n.id, { x: ci * (NODE_W + COL_GAP), y: 30 + offset + ri * (NODE_H + ROW_GAP) }));
    });
    return { pos, height, width: LAYERS.length * (NODE_W + COL_GAP) - COL_GAP };
  }, [graph]);
  const related = useMemo(() => {
    if (!hover) return null;
    const set = new Set([hover]);
    const walk = (dir: "up" | "down") => {
      let frontier = [hover];
      while (frontier.length) {
        const next: string[] = [];
        for (const e of graph.edges) {
          const [from, to] = dir === "down" ? [e.from, e.to] : [e.to, e.from];
          if (frontier.includes(from) && !set.has(to)) {
            set.add(to);
            next.push(to);
          }
        }
        frontier = next;
      }
    };
    walk("up");
    walk("down");
    return set;
  }, [hover, graph.edges]);
  return (
    <div className="dp-lineage-scroll">
      <svg width={layout.width} height={layout.height} className="dp-lineage">
        {LAYERS.map((layer, i) => (
          <text key={layer} x={i * (NODE_W + COL_GAP)} y={14} className="dp-lineage-col">
            {LAYER_TITLE[layer]}
          </text>
        ))}
        {graph.edges.map((e) => {
          const a = layout.pos.get(e.from);
          const b = layout.pos.get(e.to);
          if (!a || !b) return null;
          const x1 = a.x + NODE_W;
          const y1 = a.y + NODE_H / 2;
          const x2 = b.x;
          const y2 = b.y + NODE_H / 2;
          const on = related ? related.has(e.from) && related.has(e.to) : false;
          return (
            <path
              key={`${e.from}>${e.to}`}
              d={`M${x1},${y1} C${x1 + COL_GAP / 2},${y1} ${x2 - COL_GAP / 2},${y2} ${x2},${y2}`}
              className={`dp-edge${on ? " dp-edge--on" : ""}${related && !on ? " dp-edge--dim" : ""}`}
            />
          );
        })}
        {graph.nodes.map((n) => {
          const p = layout.pos.get(n.id);
          if (!p) return null;
          const dim = related && !related.has(n.id);
          return (
            <g
              key={n.id}
              transform={`translate(${p.x},${p.y})`}
              className={`dp-node dp-node--${n.layer}${dim ? " dp-node--dim" : ""}`}
              onMouseEnter={() => setHover(n.id)}
              onMouseLeave={() => setHover(null)}
            >
              <title>{`${n.kind}: ${n.label}${n.description ? `\n${n.description}` : ""}${n.tests ? `\n${n.tests} tests` : ""}`}</title>
              <rect width={NODE_W} height={NODE_H} rx={6} />
              <text x={8} y={17}>
                {n.label.length > 24 ? `${n.label.slice(0, 23)}…` : n.label}
              </text>
              {n.tests ? (
                <text x={NODE_W - 8} y={17} textAnchor="end" className="dp-node-tests">
                  ✓{n.tests}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
