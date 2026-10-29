import { useEffect, useState } from "react";
import { RecordDrawer } from "./DataFlowView";
import { formatMetric, type DatasetInfo, type QuarantineItem, type RecordListItem, type SqlResult } from "./dataApi";
import type { DataProductProps } from "./DataProductView";
import { Icon } from "./Icon";

type Section = "records" | "datasets" | "sql" | "quarantine";

/**
 * Exploration doors of a data product, each governed by data-api for the current role:
 * records as received (bronze, with their trail), the curated datasets, governed SQL over
 * them, and — for data stewards — the quarantine, where a rejected record is fixed and
 * replayed through the real pipeline.
 */
export function DataExploreView(props: DataProductProps) {
  const access = props.overview?.access;
  const sections: { key: Section; label: string; show: boolean }[] = [
    { key: "records", label: "Records", show: !!access?.layers.includes("bronze") },
    { key: "datasets", label: "Datasets", show: true },
    { key: "sql", label: "SQL", show: !!access?.sql },
    { key: "quarantine", label: "Quarantine", show: !!access?.layers.includes("quarantine") },
  ];
  const visible = sections.filter((s) => s.show);
  const [section, setSection] = useState<Section>("datasets");
  const current = visible.some((s) => s.key === section) ? section : (visible[0]?.key ?? "datasets");
  return (
    <div className="tab-panel">
      <div className="dp-segmented dp-segmented--section">
        {visible.map((s) => (
          <button key={s.key} className={current === s.key ? "active" : ""} onClick={() => setSection(s.key)}>
            {s.label}
          </button>
        ))}
        {sections
          .filter((s) => !s.show)
          .map((s) => (
            <button key={s.key} disabled title="Not available for your role">
              {s.label}
            </button>
          ))}
      </div>
      {current === "records" && <RecordsSection {...props} />}
      {current === "datasets" && <DatasetsSection {...props} />}
      {current === "sql" && <SqlSection {...props} />}
      {current === "quarantine" && <QuarantineSection {...props} />}
    </div>
  );
}

function RecordsSection({ scenario, client }: DataProductProps) {
  const records = scenario.data_product!.records;
  const [entity, setEntity] = useState(records[0]?.entity ?? "");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<RecordListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const search = (term: string) =>
    client
      .records(entity, term)
      .then((r) => (setRows(r), setError(null)))
      .catch((e: Error) => setError(e.message));
  useEffect(() => {
    void search("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, entity]);
  const spec = records.find((r) => r.entity === entity);
  return (
    <div className="panel-card">
      <div className="dp-search">
        <select value={entity} onChange={(e) => setEntity(e.target.value)}>
          {records.map((r) => (
            <option key={r.entity} value={r.entity}>
              {r.label}
            </option>
          ))}
        </select>
        <input
          placeholder={`Search ${spec?.label.toLowerCase() ?? ""}s: key, ${spec?.search.slice(1).join(", ")}`}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && search(q)}
        />
        <button className="dp-btn" onClick={() => search(q)}>
          Search
        </button>
      </div>
      {error && <p className="panel-hint">{error}</p>}
      <table className="dp-table dp-table--click">
        <thead>
          <tr>
            <th>Key</th>
            {spec && Object.keys(spec.summary).map((k) => <th key={k}>{k}</th>)}
            <th>Arrived</th>
            <th>Via</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.key}-${r.version}`} onClick={() => setSelected(r.key)}>
              <td>
                <code>{r.key}</code>
                {r.version > 1 && <span className="dp-role-pill dp-role-pill--muted">v{r.version}</span>}
              </td>
              {spec &&
                Object.keys(spec.summary).map((k) => (
                  <td key={k}>{k.toLowerCase().includes("total") ? formatMetric(Number(r.summary[k]), "currency_cents") : String(r.summary[k] ?? "—")}</td>
                ))}
              <td>{new Date(r.received_at).toLocaleString()}</td>
              <td>{r.source_kind}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected && <RecordDrawer client={client} entity={entity} recordKey={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function DatasetsSection({ client }: DataProductProps) {
  const [datasets, setDatasets] = useState<DatasetInfo[] | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [rows, setRows] = useState<{ columns: string[]; rows: Record<string, unknown>[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    client.datasets().then(setDatasets).catch((e: Error) => setError(e.message));
  }, [client]);
  useEffect(() => {
    if (!active) return;
    setRows(null);
    client
      .datasetRows(active, 50)
      .then((r) => (setRows(r), setError(null)))
      .catch((e: Error) => setError(e.message));
  }, [client, active]);
  return (
    <div className="dp-datasets">
      <div className="panel-card dp-dataset-list">
        <h4>Curated datasets</h4>
        {!datasets && <p className="panel-hint">Loading…</p>}
        {datasets?.map((d) => (
          <button key={d.name} className={`dp-dataset${active === d.name ? " dp-dataset--active" : ""}`} onClick={() => setActive(d.name)}>
            <span className={`dp-layer dp-layer--${d.layer}`}>{d.layer}</span>
            <b>{d.label}</b>
            <span className="panel-hint">
              {d.grain}
              {d.rows !== undefined && ` · ${d.rows.toLocaleString()} rows`}
              {d.restricted && " · restricted for your role"}
              {!d.built && " · not built yet"}
            </span>
            {d.freshness && (
              <span className={`dp-fresh${d.freshness.ok ? "" : " dp-fresh--late"}`}>
                {d.freshness.ok ? "fresh" : "late"} · {d.freshness.age_minutes} min (SLO {d.freshness_minutes})
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="panel-card dp-dataset-rows">
        {!active && <p className="panel-hint">Pick a dataset to see its latest rows (masked and filtered for your role).</p>}
        {error && <p className="panel-hint">{error}</p>}
        {active && rows && <ResultTable columns={rows.columns} rows={rows.rows} />}
      </div>
    </div>
  );
}

function ResultTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  return (
    <div className="dp-table-scroll">
      <table className="dp-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c}>{typeof row[c] === "object" && row[c] !== null ? JSON.stringify(row[c]) : String(row[c] ?? "—")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SqlSection({ scenario, client }: DataProductProps) {
  const dp = scenario.data_product!;
  const gold = dp.datasets.filter((d) => d.layer === "gold");
  const first = gold[0];
  const examples = [
    first && `SELECT * FROM ${first.name}${first.time_column ? ` ORDER BY ${first.time_column} DESC` : ""} LIMIT 20`,
    ...dp.metrics.slice(0, 3).map((m) => {
      const ds = dp.datasets.find((d) => d.name === m.dataset)!;
      const dim = m.dimensions[0];
      return dim
        ? `SELECT ${dim}, ${m.expression} AS ${m.name} FROM ${ds.name} GROUP BY ${dim} ORDER BY 2 DESC`
        : `SELECT ${m.expression} AS ${m.name} FROM ${ds.name}`;
    }),
  ].filter(Boolean) as string[];
  const [sql, setSql] = useState(examples[1] ?? examples[0] ?? "");
  const [result, setResult] = useState<SqlResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      setResult(await client.query(sql));
    } catch (e) {
      setError((e as Error).message);
      setResult(null);
    } finally {
      setRunning(false);
    }
  };
  return (
    <div className="panel-card">
      <p className="panel-hint">
        Governed SQL over the curated datasets by name ({dp.datasets.filter((d) => d.layer !== "ops").map((d) => d.name).join(", ")}). Every table
        is rewritten to your organisation's rows, read-only, with a timeout and a row cap.
      </p>
      <div className="dp-sql-examples">
        {examples.map((e) => (
          <button key={e} className="dp-chip" onClick={() => setSql(e)} title={e}>
            {e.length > 60 ? `${e.slice(0, 60)}…` : e}
          </button>
        ))}
      </div>
      <textarea className="dp-sql" value={sql} onChange={(e) => setSql(e.target.value)} rows={5} spellCheck={false} />
      <button className="dp-btn" onClick={run} disabled={running}>
        <Icon name="target" size={14} /> {running ? "Running…" : "Run"}
      </button>
      {error && <p className="dp-error">{error}</p>}
      {result && (
        <>
          <details>
            <summary>
              Executed on {result.engine} ({result.rows.length} rows{result.truncated ? ", truncated" : ""})
            </summary>
            <pre className="dp-json">{result.executed_sql}</pre>
          </details>
          <ResultTable columns={result.columns} rows={result.rows} />
        </>
      )}
    </div>
  );
}

function QuarantineSection({ client, overview, refresh }: DataProductProps) {
  const [items, setItems] = useState<QuarantineItem[] | null>(null);
  const [editing, setEditing] = useState<Record<number, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const load = () => client.quarantine("open").then(setItems).catch((e: Error) => setMessage(e.message));
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);
  const resolve = async (item: QuarantineItem, discard: boolean) => {
    let payload: Record<string, unknown> | null = null;
    if (!discard && editing[item.quarantine_id] !== undefined) {
      try {
        payload = JSON.parse(editing[item.quarantine_id]);
      } catch {
        setMessage("The fixed record is not valid JSON.");
        return;
      }
    }
    try {
      const r = await client.resolveQuarantine(item.quarantine_id, { payload, discard });
      setMessage(`#${r.quarantine_id} ${r.status}${r.status === "replayed" ? " — watch it on the Live flow tab" : ""}`);
      await load();
      refresh();
    } catch (e) {
      setMessage((e as Error).message);
    }
  };
  return (
    <div className="panel-card">
      <p className="panel-hint">
        Records rejected at ingestion with every rule they failed ({overview?.ops.quarantine?.open ?? 0} open). Fix the payload and replay it: it goes
        back through the real topic, contract and bronze — same key, so no duplicate.
      </p>
      {message && <div className="dp-sim-message">{message}</div>}
      {items?.length === 0 && <p className="panel-hint">Nothing in quarantine — try “Malformed sale” on the Live flow tab.</p>}
      {items?.map((item) => (
        <div key={item.quarantine_id} className="dp-quarantine-item">
          <div className="dp-action-head">
            <b>
              #{item.quarantine_id} <code>{item.business_key ?? "(no key)"}</code>
            </b>
            <span className="panel-hint">
              {item.source_id} · {item.source_kind}
              {item.delivery ? ` · ${item.delivery}` : ""} · {new Date(item.received_at).toLocaleString()}
            </span>
          </div>
          <ul>
            {item.violations.map((v) => (
              <li key={v.rule + v.message}>
                <code>{v.rule}</code> {v.message}
              </li>
            ))}
          </ul>
          <textarea
            className="dp-sql"
            rows={6}
            spellCheck={false}
            value={editing[item.quarantine_id] ?? JSON.stringify(item.payload ?? item.raw, null, 2)}
            onChange={(e) => setEditing((ed) => ({ ...ed, [item.quarantine_id]: e.target.value }))}
          />
          <div className="dp-live-row">
            <button className="dp-btn" onClick={() => resolve(item, false)}>
              Replay {editing[item.quarantine_id] !== undefined ? "fixed record" : "as is"}
            </button>
            <button className="dp-btn dp-btn--ghost" onClick={() => resolve(item, true)}>
              Discard
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
