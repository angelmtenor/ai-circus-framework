/**
 * Generic 5th workspace tab (`ui_extras: {kind: message_screening}`, see scenario_schema.MessageScreeningExtra):
 * an inbox of messages (e.g. outgoing credit transfers) sorted by their out-of-fold score; the selected one
 * drawn as the message itself — each field tinted by its SHAP push, the free text word by word — next to
 * three readers: the deployed model, the transformer challenger and, when the scenario has a `rubric_check`,
 * an LLM reading the same message against a red-flag rubric. Then the real outcome and its typology.
 *
 * Scores and field pushes are the precomputed out-of-fold ones (a model that never saw the message); the
 * word shading and the model duel are live calls for the selected message only, the LLM only on request.
 * DOM only, no frame loop, the inbox draws at most INBOX_ROWS rows.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import {
  datasetSample,
  outOfFoldScores,
  rubricCheck,
  type MessageScreeningExtra,
  type RubricCheckConfig,
  type RubricCheckResult,
  type ScenarioSummary,
} from "./apiClient";
import { BarList, CHART_COLORS, StatTile } from "./charts";
import { config } from "./config";
import { explainByFeature, featureLabel, type Record_ } from "./predictUtils";
import { LlmReading } from "./RubricCheckPanel";
import { probabilityPoints, TokenHighlights, TopWords } from "./textHighlights";
import { ModelDuel, scoreTextRecord, type TextScore } from "./textModels";
import "./messageScreening.css";

const INBOX_ROWS = 60;
const LLM_CACHE = 30;

type Row = Record<string, string | number | null>;
type Item = { id: string; probability: number; actual: 0 | 1; row: Row; push: Map<string, number> };
type Filter = "escalate" | "release" | "all";

const pct = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`;

function formatValue(scenario: ScenarioSummary, column: string, value: string | number | null): string {
  if (value === null || value === undefined || value === "") return "—";
  const spec = scenario.feature_schema?.[column];
  if (spec?.type === "numeric") {
    const n = Number(value);
    if (spec.min === 0 && spec.max === 1) return n >= 0.5 ? "Yes" : "No";
    return n.toLocaleString("en-GB", { maximumFractionDigits: 2 });
  }
  return String(value);
}

function columnLabel(scenario: ScenarioSummary, column: string): string {
  if (scenario.feature_schema?.[column]) return featureLabel(scenario, column);
  return column.replace(/([a-z])([A-Z])/g, "$1 $2");
}

/** The message as plain text, the way the LLM reads it — every field, never the outcome or typology. */
function messageText(scenario: ScenarioSummary, extras: MessageScreeningExtra, item: Item): string {
  const lines = [`${extras.format_label} ${extras.item_noun} ${item.id}`];
  for (const field of extras.fields) {
    const parts = field.columns.map((c) => `${columnLabel(scenario, c)}: ${formatValue(scenario, c, item.row[c])}`);
    lines.push(`${field.tag} ${field.label} — ${parts.join("; ")}`);
  }
  lines.push(`${extras.text_tag} ${featureLabel(scenario, extras.text_feature)} — ${String(item.row[extras.text_feature] ?? "")}`);
  return lines.join("\n");
}

function pushColor(value: number, max: number): string | undefined {
  const strength = Math.round((Math.abs(value) / Math.max(max, 1e-9)) * 55);
  if (strength < 5) return undefined;
  return `color-mix(in srgb, ${value >= 0 ? "var(--red)" : "var(--blue)"} ${strength}%, transparent)`;
}

export function MessageScreeningView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as MessageScreeningExtra;
  const rubric = scenario.rubric_check as RubricCheckConfig | null | undefined;
  const items = `${extras.item_noun}s`;
  const [inbox, setInbox] = useState<Item[] | null>(null);
  const [rocAuc, setRocAuc] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(extras.review_at);
  const [filter, setFilter] = useState<Filter>("escalate");
  const [query, setQuery] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [score, setScore] = useState<TextScore | null>(null);
  const [scoreError, setScoreError] = useState<string | null>(null);
  const [llm, setLlm] = useState<RubricCheckResult | null>(null);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [llmRunning, setLlmRunning] = useState(false);
  const llmCache = useRef(new Map<string, RubricCheckResult>());
  const openRef = useRef<string | null>(null);
  useEffect(() => {
    openRef.current = openId;
  }, [openId]);

  useEffect(() => {
    let live = true;
    const features = scenario.feature_columns ?? [];
    Promise.all([outOfFoldScores(config.predictionUrl, scenario.slug, accessToken), datasetSample(config.predictionUrl, scenario.slug, 30000, accessToken)])
      .then(([scores, sample]) => {
        if (!live) return;
        const target = scenario.target ?? "";
        const column = sample.id_column ?? "";
        const rows = new Map(sample.rows.map((row) => [String(row[column]), row] as const));
        const list = scores.rows.flatMap((r): Item[] => {
          const row = rows.get(r.id);
          if (!row) return [];
          const push = new Map(explainByFeature(features, r.probability, r.contributions).items.map((i) => [i.feature, i.value]));
          return [{ id: r.id, probability: r.probability, actual: Number(row[target]) === 1 ? 1 : 0, row, push }];
        });
        list.sort((a, b) => b.probability - a.probability);
        setInbox(list);
        setRocAuc(scores.roc_auc);
        setOpenId(list[0]?.id ?? null);
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [scenario.slug, scenario.target, scenario.feature_columns, accessToken]);

  const open = useMemo(() => inbox?.find((i) => i.id === openId) ?? null, [inbox, openId]);

  // Live: the deployed model's word shading and the challenger, for the open message only.
  useEffect(() => {
    if (!open) return;
    let live = true;
    setScore(null);
    setScoreError(null);
    setLlm(llmCache.current.get(open.id) ?? null);
    setLlmError(null);
    const record: Record_ = {};
    for (const f of scenario.feature_columns ?? []) record[f] = (open.row[f] ?? scenario.feature_schema?.[f]?.default ?? "") as number | string;
    scoreTextRecord(scenario, record, accessToken, true).then(
      (s) => live && setScore(s),
      (e: Error) => live && setScoreError(e.message),
    );
    return () => {
      live = false;
    };
  }, [open, scenario, accessToken]);

  const kpis = useMemo(() => {
    const all = inbox ?? [];
    const escalate = all.filter((i) => i.probability >= threshold);
    const suspicious = all.filter((i) => i.actual === 1);
    const missed = suspicious.filter((i) => i.probability < threshold).length;
    return { total: all.length, escalate: escalate.length, suspicious: suspicious.length, missed, needless: escalate.filter((i) => i.actual === 0).length };
  }, [inbox, threshold]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (inbox ?? []).filter((i) => {
      if (filter === "escalate" && i.probability < threshold) return false;
      if (filter === "release" && i.probability >= threshold) return false;
      if (!q) return true;
      return i.id.toLowerCase().includes(q) || String(i.row[extras.text_feature] ?? "").toLowerCase().includes(q);
    });
  }, [inbox, filter, threshold, query, extras.text_feature]);

  const typologies = useMemo(() => {
    const column = extras.typology_column;
    if (!column || !inbox) return [];
    const groups = new Map<string, { n: number; flagged: number; real: number }>();
    for (const i of inbox) {
      const key = String(i.row[column] ?? "—");
      const g = groups.get(key) ?? { n: 0, flagged: 0, real: 0 };
      g.n += 1;
      g.flagged += i.probability >= threshold ? 1 : 0;
      g.real += i.actual;
      groups.set(key, g);
    }
    return [...groups.entries()].map(([label, g]) => ({ label, ...g })).sort((a, b) => b.real / b.n - a.real / a.n || b.n - a.n);
  }, [inbox, threshold, extras.typology_column]);

  useCopilotReadable({
    description: `${scenario.title} message screening on screen: ${items} sorted by the out-of-fold probability of "${scenario.target_value_labels?.["1"] ?? "positive"}", the open one with its fields.`,
    value: open
      ? {
          threshold,
          to_escalate: kpis.escalate,
          open_message: messageText(scenario, extras, open),
          out_of_fold_probability: open.probability,
          revealed_outcome: revealed ? (scenario.target_value_labels?.[String(open.actual)] ?? open.actual) : "not revealed",
          llm_reading: llm ? { verdict: llm.verdict_label, summary: llm.summary } : null,
        }
      : null,
  });

  async function askLlm() {
    if (!open || !rubric) return;
    setLlmRunning(true);
    setLlmError(null);
    const id = open.id;
    try {
      const reading = await rubricCheck(config.assistantUrl, scenario.slug, messageText(scenario, extras, open).slice(0, rubric.max_chars), accessToken);
      if (llmCache.current.size >= LLM_CACHE) llmCache.current.delete(llmCache.current.keys().next().value as string);
      llmCache.current.set(id, reading);
      if (openRef.current === id) setLlm(reading);
    } catch (e) {
      setLlmError((e as Error).message);
    } finally {
      setLlmRunning(false);
    }
  }

  if (error) return <div className="tab-panel"><div className="error">Could not load the {extras.item_noun} inbox: {error}</div></div>;
  if (!inbox) return <div className="tab-panel"><div className="panel-hint">Loading the {extras.item_noun} inbox…</div></div>;

  const yes = scenario.target_value_labels?.["1"] ?? "Positive";
  const no = scenario.target_value_labels?.["0"] ?? "Negative";
  const maxPush = open ? Math.max(1e-9, ...[...open.push.values()].map(Math.abs)) : 1;
  const fieldPush = (columns: string[]) => columns.reduce((sum, c) => sum + (open?.push.get(c) ?? 0), 0);
  const textTokens = score?.champion.text_explanations?.[extras.text_feature];
  const escalating = open ? open.probability >= threshold : false;
  const drivers = open
    ? [...open.push.entries()]
        .filter(([, v]) => Math.abs(v) >= 0.002)
        .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
        .slice(0, 8)
        .map(([feature, value]) => ({ label: featureLabel(scenario, feature), value }))
    : [];

  return (
    <div className="tab-panel msgscreen">
      <div className="msgscreen-head">
        <div>
          <h2>{extras.title}</h2>
          {extras.subtitle && <p className="panel-hint">{extras.subtitle}</p>}
        </div>
        <button className={revealed ? "btn-secondary" : "btn-primary"} onClick={() => setRevealed((r) => !r)}>
          {revealed ? "Hide the investigation" : "Reveal the investigation"}
        </button>
      </div>

      <div className="kpi-row msgscreen-kpis">
        <StatTile label={`${items} screened`} value={kpis.total.toLocaleString("en-GB")} sub={rocAuc !== null ? `ROC AUC ${rocAuc.toFixed(3)}, out of fold` : undefined} />
        <StatTile
          label={`to ${extras.escalate_label.toLowerCase()}`}
          value={`${kpis.escalate} (${pct(kpis.total ? kpis.escalate / kpis.total : 0)})`}
          sub={revealed ? `${kpis.needless} of them released after investigation` : `${extras.reviewer_noun} workload at this threshold`}
        />
        <StatTile
          label="Missed"
          value={revealed ? `${kpis.missed} of ${kpis.suspicious}` : "hidden"}
          sub={revealed ? `${yes.toLowerCase()} but below the threshold` : "reveal the investigation to see"}
          color={revealed ? (kpis.missed ? CHART_COLORS.red : CHART_COLORS.green) : undefined}
          highlight
        />
      </div>

      <div className="panel-card msgscreen-threshold">
        <label>
          {extras.escalate_label} to an {extras.reviewer_noun} from a probability of <strong>{pct(threshold)}</strong>
          <input type="range" min={0.05} max={0.95} step={0.05} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
        </label>
      </div>

      <div className="msgscreen-grid">
        <aside className="panel-card msgscreen-inbox" aria-label={`${extras.item_noun} inbox`}>
          <div className="msgscreen-filters" role="group" aria-label="Filter">
            {(
              [
                ["escalate", `${extras.escalate_label} · ${kpis.escalate}`],
                ["release", `${extras.release_label} · ${kpis.total - kpis.escalate}`],
                ["all", `All · ${kpis.total}`],
              ] as [Filter, string][]
            ).map(([key, label]) => (
              <button key={key} className={`filter-chip${filter === key ? " active" : ""}`} onClick={() => setFilter(key)}>
                {label}
              </button>
            ))}
          </div>
          <input
            className="msgscreen-search"
            type="search"
            placeholder={`Search a reference or a word…`}
            value={query}
            maxLength={60}
            onChange={(e) => setQuery(e.target.value)}
          />
          <ul className="msgscreen-list">
            {visible.slice(0, INBOX_ROWS).map((item) => {
              const hit = item.probability >= threshold;
              const right = hit === (item.actual === 1);
              return (
                <li key={item.id}>
                  <button className={`msgscreen-item${item.id === openId ? " msgscreen-item--on" : ""}`} onClick={() => setOpenId(item.id)} aria-pressed={item.id === openId}>
                    <span className="msgscreen-item-top">
                      <span className="msgscreen-ref">{item.id}</span>
                      <span className={`msgscreen-score${hit ? " msgscreen-score--hot" : ""}`}>{pct(item.probability)}</span>
                      {revealed && (
                        <span className={right ? "msgscreen-ok" : "msgscreen-bad"} title={item.actual ? yes : no}>
                          {right ? "✓" : "✗"}
                        </span>
                      )}
                    </span>
                    <span className="msgscreen-snippet">{String(item.row[extras.text_feature] ?? "")}</span>
                    <span className="msgscreen-bar">
                      <span style={{ width: pct(item.probability), background: hit ? "var(--red)" : "var(--blue)" }} />
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="panel-hint">
            {visible.length > INBOX_ROWS ? `Top ${INBOX_ROWS} of ${visible.length} shown, highest score first.` : `${visible.length} shown, highest score first.`}
          </p>
        </aside>

        {open && (
          <section className="panel-card msgscreen-message" aria-label={`${extras.item_noun} ${open.id}`}>
            <div className="msgscreen-message-head">
              <span className="msgscreen-format">{extras.format_label}</span>
              <span className={`msgscreen-lane${escalating ? " msgscreen-lane--hot" : ""}`}>
                {escalating ? extras.escalate_label : extras.release_label} · {pct(open.probability)}
              </span>
            </div>
            <div className="msgscreen-swift">
              <div className="msgscreen-line">
                <span className="msgscreen-tag">:20:</span>
                <span className="msgscreen-label">Reference</span>
                <span className="msgscreen-values">{open.id}</span>
              </div>
              {extras.fields.map((field) => {
                const push = fieldPush(field.columns);
                const scored = field.columns.some((c) => open.push.has(c));
                return (
                  <div key={field.tag} className="msgscreen-line" style={{ background: scored ? pushColor(push, maxPush) : undefined }}>
                    <span className="msgscreen-tag">{field.tag}</span>
                    <span className="msgscreen-label">{field.label}</span>
                    <span className="msgscreen-values">
                      {field.columns.map((c) => (
                        <span key={c} className="msgscreen-value" title={open.push.has(c) ? `${columnLabel(scenario, c)}: ${probabilityPoints(open.push.get(c) ?? 0)}` : columnLabel(scenario, c)}>
                          {/* An identity column (a display column) needs no label: the field names it. */}
                          {scenario.feature_schema?.[c] && <small>{columnLabel(scenario, c)}</small>} {formatValue(scenario, c, open.row[c])}
                        </span>
                      ))}
                    </span>
                    {scored && <span className="msgscreen-push">{probabilityPoints(push)}</span>}
                  </div>
                );
              })}
              <div className="msgscreen-line msgscreen-line--text" style={{ background: pushColor(open.push.get(extras.text_feature) ?? 0, maxPush) }}>
                <span className="msgscreen-tag">{extras.text_tag}</span>
                <span className="msgscreen-label">{featureLabel(scenario, extras.text_feature)}</span>
                <span className="msgscreen-values msgscreen-text">
                  {textTokens ? (
                    <TokenHighlights text={String(open.row[extras.text_feature] ?? "")} tokens={textTokens} format={probabilityPoints} />
                  ) : (
                    String(open.row[extras.text_feature] ?? "")
                  )}
                </span>
                <span className="msgscreen-push">{probabilityPoints(open.push.get(extras.text_feature) ?? 0)}</span>
              </div>
            </div>
            <p className="panel-hint">
              Each field is tinted by how much it pushed this {extras.item_noun}'s score (<span className="msgscreen-red">red</span> towards “{yes}”,{" "}
              <span className="msgscreen-blue">blue</span> away), from a model that never saw it. The words are shaded live by the deployed model.
            </p>
            {textTokens && <TopWords tokens={textTokens} format={probabilityPoints} />}
            {revealed && (
              <div className={`msgscreen-verdict${open.actual ? " msgscreen-verdict--hot" : ""}`}>
                <strong>Investigation: {open.actual ? yes : no}</strong>
                {extras.typology_column && <span> · {String(open.row[extras.typology_column] ?? "—")}</span>}
              </div>
            )}
          </section>
        )}

        {open && (
          <section className="msgscreen-readers" aria-label="Readers">
            <div className="panel-card">
              <h3>What pushed the score</h3>
              <BarList items={drivers} valueFormatter={(v) => probabilityPoints(v)} />
            </div>
            <div className="panel-card">
              <h3>Two trained models</h3>
              {score ? <ModelDuel scenario={scenario} score={score} /> : scoreError ? <p className="error">{scoreError}</p> : <div className="app-loading">Scoring…</div>}
              <p className="panel-hint">The deployed model was refit on every {extras.item_noun}, so its score of a known one is optimistic: the inbox uses the out-of-fold score.</p>
            </div>
            {rubric && (
              <div className="panel-card msgscreen-llm">
                <h3>{rubric.title}</h3>
                {llm ? (
                  <LlmReading reading={llm} rubric={rubric} />
                ) : (
                  <>
                    <p className="panel-hint">The active LLM reads this {extras.item_noun} against a rubric of red flags and legitimate indicators, quoting it as evidence. It never sees the score or the outcome.</p>
                    <button className="btn-primary" onClick={askLlm} disabled={llmRunning}>
                      {llmRunning ? "Reading…" : `Ask the ${rubric.title}`}
                    </button>
                    {llmError && <p className="error">{llmError}</p>}
                  </>
                )}
              </div>
            )}
          </section>
        )}
      </div>

      {revealed && typologies.length > 0 && (
        <div className="panel-card msgscreen-typologies">
          <h3>By typology, at a threshold of {pct(threshold)}</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Typology</th>
                <th>{items}</th>
                <th>Really {yes.toLowerCase()}</th>
                <th>Sent to an {extras.reviewer_noun}</th>
              </tr>
            </thead>
            <tbody>
              {typologies.map((t) => (
                <tr key={t.label}>
                  <td>{t.label}</td>
                  <td>{t.n}</td>
                  <td>{pct(t.real / t.n)}</td>
                  <td>
                    <span className="msgscreen-rate">
                      <span style={{ width: pct(t.flagged / t.n), background: t.real / t.n >= 0.5 ? "var(--red)" : "var(--blue)" }} />
                    </span>
                    {pct(t.flagged / t.n)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="panel-hint">The typology is what the investigation found: shown to explain the outcomes, never a model input.</p>
        </div>
      )}
    </div>
  );
}
