import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import {
  datasetSample,
  extractDocument,
  extractRecord,
  intakeSample,
  modelCard,
  outOfFoldScores,
  type CaseDeskExtra,
  type Decision,
  type DecisionPolicy,
  type ExtractedRecord,
  type PredictionResult,
  type RuleOutcome,
  type RuleResult,
  type ScenarioSummary,
} from "./apiClient";
import { CaseDeskCircuit } from "./CaseDeskCircuit";
import {
  DESK_PALETTE,
  deskStats,
  equityRows,
  formatNumber,
  joinCases,
  pct,
  TRAY_GLYPHS,
  trayOf,
  TRAYS,
  withComputedFields,
  WORDS,
  type DeskCase,
  type Tray,
} from "./caseDeskLogic";
import { CaseSheet, DecisionPanel, type ExtractionView, type SheetState } from "./CaseSheet";
import { config } from "./config";
import { NormativaDesk, type LawFocus } from "./NormativaDesk";
import { initialRecord, type Record_ } from "./predictUtils";
import { surfaceMode } from "./riskPalette";
import { scoreTextRecord, type TextScore } from "./textModels";
import { useTheme } from "./useTheme";
import "./caseDesk.css";

/**
 * Generic 5th workspace tab for a tabular_ml scenario whose rows are *applications*
 * decided by business rules + a binary model + a decision policy (`ui_extras:
 * {kind: case_desk}`, see scenario_schema.CaseDeskExtra) — e.g. `prestaciones_sociales`.
 *
 * The circuit sends every application through the rules gate and the model into three
 * proposal trays (honest, cross-fitted scores; thresholds are live sliders). One opens
 * as an official paper sheet — rule checklist, the boxes lit by how much they pushed the
 * proposal, words highlighted — or a new application is typed, started from a persona,
 * or read from a scanned document (OCR + LLM extraction). Outcome rates are audited per
 * protected group. The wording is the scenario's; the desk's chrome follows its locale.
 * A scenario whose assistant searches its regulations (`documents.tool`) adds a reading
 * room (NormativaDesk.tsx), reachable from any fired rule's legal basis ("📖").
 */

type Loaded = {
  cases: DeskCase[];
  oofAuc: number | null;
  cvAuc: number | null;
  holdoutAuc: number | null;
  trainable: number;
};

const CACHE = new Map<string, Promise<Loaded>>();

async function loadDesk(scenario: ScenarioSummary, extras: CaseDeskExtra, accessToken: string | null): Promise<Loaded> {
  const [sample, oof, card] = await Promise.all([
    datasetSample(config.predictionUrl, scenario.slug, 5000, accessToken),
    outOfFoldScores(config.predictionUrl, scenario.slug, accessToken),
    modelCard(config.predictionUrl, scenario.slug, accessToken).catch(() => null),
  ]);
  const idColumn = sample.id_column ?? "id";
  const columns = [...(scenario.feature_columns ?? []), ...Object.keys(scenario.rule_columns ?? {})];
  const cases = joinCases(
    sample.rows,
    idColumn,
    extras.name_column,
    columns,
    extras.equity_columns,
    scenario.target ?? "",
    sample.gates,
    sample.fired_rules,
    new Map(oof.rows.map((r) => [r.id, r])),
  );
  return {
    cases,
    oofAuc: oof.roc_auc,
    cvAuc: card?.metrics.cv_roc_auc_mean ?? null,
    holdoutAuc: card?.metrics.holdout_roc_auc ?? null,
    trainable: oof.rows.length,
  };
}

function decideLocal(policy: Pick<DecisionPolicy, "approve_at" | "deny_at">, p: number | null, gate: RuleOutcome | null): Decision | null {
  if (gate !== null) return gate;
  if (p === null) return null;
  return p >= policy.approve_at ? "approve" : p <= policy.deny_at ? "deny" : "review";
}

type Mode = "portfolio" | "fresh" | "scan" | "normativa";
type ScanPhase = "idle" | "ocr" | "llm" | "reading" | "done" | "error";

const EMPTY_SCORE: TextScore | null = null;

export function CaseDeskView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as CaseDeskExtra;
  const basePolicy = scenario.decision_policy as DecisionPolicy;
  const w = WORDS[extras.locale];
  const { theme } = useTheme();
  const mode = surfaceMode(theme.cssVars["--bg"]);
  const palette = DESK_PALETTE[mode];
  const ink = theme.cssVars["--text-h"] ?? (mode === "dark" ? "#f3fbff" : "#0b1f33");
  const dim = theme.cssVars["--dim"] ?? "#8ea4c0";

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approveAt, setApproveAt] = useState(basePolicy.approve_at);
  const [denyAt, setDenyAt] = useState(basePolicy.deny_at);
  const [revealed, setRevealed] = useState(false);
  const [view, setView] = useState<Mode>("portfolio");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [fresh, setFresh] = useState<Record_>(() =>
    withComputedFields(initialRecord(scenario.feature_columns ?? [], scenario.feature_schema ?? {}), extras.computed_fields),
  );
  const [freshLabel, setFreshLabel] = useState<string | null>(null);
  const [score, setScore] = useState<TextScore | null>(EMPTY_SCORE);
  const [scoring, setScoring] = useState(false);
  const [scoreError, setScoreError] = useState<string | null>(null);
  const [scan, setScan] = useState<{ phase: ScanPhase; fileName?: string; error?: string; result?: ExtractedRecord; revealedBoxes: number }>({
    phase: "idle",
    revealedBoxes: 0,
  });
  const [stampSeq, setStampSeq] = useState(0);
  const [lawFocus, setLawFocus] = useState<LawFocus | null>(null);
  const workbench = useRef<HTMLDivElement>(null);
  const documentTool = scenario.document_tool ?? null;

  const policy: DecisionPolicy = useMemo(() => ({ ...basePolicy, approve_at: approveAt, deny_at: denyAt }), [basePolicy, approveAt, denyAt]);

  useEffect(() => {
    const key = `${scenario.slug}|${accessToken ?? ""}`;
    if (!CACHE.has(key)) CACHE.set(key, loadDesk(scenario, extras, accessToken));
    let cancelled = false;
    CACHE.get(key)!
      .then((data) => !cancelled && setLoaded(data))
      .catch((e: Error) => {
        CACHE.delete(key);
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [scenario, extras, accessToken]);

  const cases = useMemo(() => loaded?.cases ?? [], [loaded]);
  const byId = useMemo(() => new Map(cases.map((c) => [c.id, c])), [cases]);
  const stats = useMemo(() => deskStats(cases, policy), [cases, policy]);
  const selected = selectedId ? (byId.get(selectedId) ?? null) : null;

  const trayLabels = useMemo(
    () => Object.fromEntries(TRAYS.map((t) => [t, basePolicy.labels[t]])) as Record<Tray, string>,
    [basePolicy.labels],
  );

  // ── the record on the sheet, and its live score ───────────────────────────────
  const sheetRecord: Record_ | null = useMemo(() => {
    if (view === "normativa") return null;
    if (view === "portfolio") return selected ? selected.record : null;
    if (view === "scan" && scan.phase !== "done" && scan.phase !== "reading") return null;
    return fresh;
  }, [view, selected, fresh, scan.phase]);

  const scoreSeq = useRef(0);
  const sheetKey = sheetRecord ? JSON.stringify(sheetRecord) : "";
  useEffect(() => {
    if (!sheetRecord || (view === "scan" && scan.phase !== "done")) {
      setScore(null);
      return;
    }
    const seq = ++scoreSeq.current;
    setScoring(true);
    setScoreError(null);
    const timer = setTimeout(
      () => {
        scoreTextRecord(scenario, sheetRecord, accessToken, true)
          .then((result) => seq === scoreSeq.current && (setScore(result), setStampSeq((n) => n + 1)))
          .catch((e: Error) => seq === scoreSeq.current && setScoreError(e.message))
          .finally(() => seq === scoreSeq.current && setScoring(false));
      },
      view === "portfolio" ? 0 : 450,
    );
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sheetKey stands for sheetRecord's contents
  }, [sheetKey, view, scan.phase, scenario, accessToken]);

  const sheet: SheetState | null = useMemo(() => {
    if (!sheetRecord) return null;
    const live: PredictionResult | null = score?.champion ?? null;
    const honest = view === "portfolio" && selected && selected.probability !== null;
    const probability = honest ? selected!.probability : (live?.prediction ?? null);
    const gate = live?.gate ?? (view === "portfolio" ? (selected?.gate ?? null) : null);
    return {
      record: sheetRecord,
      contributions: honest ? selected!.contributions : (live?.contributions ?? null),
      probability,
      liveProbability: live?.prediction ?? null,
      rules: live?.rules ?? null,
      gate: view === "portfolio" ? (selected?.gate ?? gate) : gate,
      decision: decideLocal(policy, probability, view === "portfolio" ? (selected?.gate ?? gate) : gate),
      tokens: live?.text_explanations ?? null,
      challenger: score?.challenger ?? null,
      challengerError: score?.challengerError ?? null,
      scoring,
      scoreSource: honest ? "honest" : "live",
      actual: view === "portfolio" && selected ? selected.actual : null,
    };
  }, [sheetRecord, score, selected, view, policy, scoring]);

  // ── editing, personas, opening cases ──────────────────────────────────────────
  const edit = useCallback(
    (field: string, value: number | string) => {
      setFresh((record) => {
        const next: Record_ = { ...record };
        if (value === "" || value === undefined) delete next[field];
        else next[field] = value;
        return withComputedFields(next, extras.computed_fields);
      });
    },
    [extras.computed_fields],
  );

  const openCase = useCallback((id: string) => {
    setView("portfolio");
    setSelectedId(id);
    workbench.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // "📖" on a fired rule: open the reading room on its legal basis and ask about it.
  const openLegalBasis = (rule: RuleResult) => {
    if (!rule.legal_basis) return;
    const caseRef = view === "portfolio" && selected ? `${extras.case_noun} ${selected.id}` : w.thisCase;
    setLawFocus({ id: Date.now(), basis: rule.legal_basis, question: w.askLegalBasis(rule.legal_basis, rule.label, caseRef) });
    setView("normativa");
    workbench.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const openFromTray = (tray: Tray) => {
    const pool = cases.filter((c) => trayOf(policy, c) === tray);
    if (pool.length) openCase(pool[Math.floor(Math.random() * pool.length)].id);
  };

  const loadPersona = (index: number) => {
    const persona = extras.personas[index];
    setView("fresh");
    setFreshLabel(persona.label);
    setFresh(withComputedFields({ ...persona.record }, extras.computed_fields));
  };

  const blank = () => {
    setView("fresh");
    setFreshLabel(null);
    setFresh(withComputedFields(initialRecord(scenario.feature_columns ?? [], scenario.feature_schema ?? {}), extras.computed_fields));
  };

  // ── scanned documents: OCR → LLM extraction → boxes appear one by one ──────────
  const boxOrder = useMemo(() => extras.sections.flatMap((s) => s.fields), [extras.sections]);
  const readFile = useCallback(
    async (file: File) => {
      setView("scan");
      setScan({ phase: "ocr", fileName: file.name, revealedBoxes: 0 });
      try {
        const doc = await extractDocument(config.platformRegistryUrl, file, accessToken);
        setScan({ phase: "llm", fileName: file.name, revealedBoxes: 0 });
        const result = await extractRecord(config.assistantUrl, scenario.slug, doc.text, accessToken);
        const record: Record_ = initialRecord(scenario.feature_columns ?? [], scenario.feature_schema ?? {});
        for (const [column, field] of Object.entries(result.fields)) record[column] = field.value;
        setFresh(withComputedFields(record, extras.computed_fields));
        setFreshLabel(null);
        setScan({ phase: "reading", fileName: file.name, result, revealedBoxes: 0 });
        let shown = 0;
        const timer = setInterval(() => {
          shown += 1;
          if (shown >= boxOrder.length) {
            clearInterval(timer);
            setScan((s) => ({ ...s, phase: "done", revealedBoxes: boxOrder.length }));
          } else setScan((s) => ({ ...s, revealedBoxes: shown }));
        }, 110);
      } catch (e) {
        setScan({ phase: "error", fileName: file.name, error: (e as Error).message, revealedBoxes: 0 });
      }
    },
    [accessToken, boxOrder.length, extras.computed_fields, scenario],
  );

  const readSample = async (filename: string) => {
    setView("scan");
    setScan({ phase: "ocr", fileName: filename, revealedBoxes: 0 });
    try {
      await readFile(await intakeSample(config.assistantUrl, scenario.slug, filename, accessToken));
    } catch (e) {
      setScan({ phase: "error", fileName: filename, error: (e as Error).message, revealedBoxes: 0 });
    }
  };

  const extraction: ExtractionView | null =
    view === "scan" && scan.result
      ? { fields: scan.result.fields, rejected: scan.result.rejected, revealed: scan.revealedBoxes, done: scan.phase === "done" }
      : null;

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return cases.filter((c) => c.id.toLowerCase().includes(q) || c.name.toLowerCase().includes(q)).slice(0, 8);
  }, [cases, query]);

  const equity = useMemo(
    () => extras.equity_columns.map((column) => ({ column, rows: equityRows(cases, column, policy) })),
    [cases, extras.equity_columns, policy],
  );

  useCopilotReadable({
    description: `${scenario.title} case desk: how every ${extras.case_noun} is sorted by the business rules and the model into proposal trays at the current thresholds, plus the ${extras.case_noun} open on the sheet (its boxes, rules, probability, decision and the boxes that weigh most). Use it to answer why an application is where it is, what is missing, or what a threshold change would do.`,
    value: loaded
      ? {
          thresholds: { approve_at: approveAt, deny_at: denyAt },
          trays: Object.fromEntries(TRAYS.map((t) => [trayLabels[t], stats.byTray[t].n])),
          automated_share_of_those_reaching_the_model: Number(stats.automatedShare.toFixed(3)),
          error_rate_in_automated_trays: Number(stats.automatedErrorRate.toFixed(3)),
          real_grant_rate_of_manual_review_tray: stats.reviewGrantRate === null ? null : Number(stats.reviewGrantRate.toFixed(3)),
          open_application: sheet
            ? {
                source: view,
                id: view === "portfolio" ? selected?.id : (freshLabel ?? "new application"),
                boxes: sheet.record,
                rules_fired: (sheet.rules ?? []).filter((r) => r.fired).map((r) => ({ rule: r.label, outcome: r.outcome, message: r.message, evidence: r.evidence })),
                gate: sheet.gate,
                probability_of_grant: sheet.probability === null ? null : Number(sheet.probability.toFixed(3)),
                decision: sheet.decision ? policy.labels[sheet.decision] : null,
                ...(extraction ? { unread_boxes: boxOrder.filter((b) => !(b in extraction.fields) && !(b in fresh)) } : {}),
                ...(sheet.actual !== null && revealed ? { real_resolution_granted: sheet.actual } : {}),
              }
            : null,
        }
      : null,
  });

  if (error) return <div className="cd-empty">No se pudo cargar la mesa: {error}</div>;
  if (!loaded) {
    return (
      <div className="cd-empty cd-loading">
        <span className="cd-spinner" /> {extras.case_noun_plural}…
      </div>
    );
  }

  const stoppedTotal = stats.byTray.request_info.n + stats.byTray.reject.n;
  const panelProps = { scenario, extras, policy, mode } as const;
  const stampKey = `${view}-${selectedId ?? freshLabel ?? "x"}-${stampSeq}`;

  return (
    <div className="cd">
      <header className="cd-head">
        <div>
          <h3>{extras.title}</h3>
          {extras.subtitle && <p>{extras.subtitle}</p>}
        </div>
        <div className="cd-model">
          <span>
            AUC <b>{loaded.oofAuc !== null ? loaded.oofAuc.toFixed(3) : "—"}</b> <em>cv</em>
          </span>
          {loaded.holdoutAuc !== null && (
            <span>
              AUC <b>{loaded.holdoutAuc.toFixed(3)}</b> <em>hold-out</em>
            </span>
          )}
          <span>
            <b>{formatNumber(loaded.trainable, extras.locale)}</b> <em>{extras.case_noun_plural}</em>
          </span>
        </div>
      </header>

      <section className="cd-card cd-hero">
        <CaseDeskCircuit
          cases={cases}
          policy={policy}
          mode={mode}
          trayLabels={trayLabels}
          nodeLabels={{ registry: w.registry, rules: w.rules, model: w.model }}
          ink={ink}
          dim={dim}
          revealed={revealed}
          selectedId={view === "portfolio" ? selectedId : null}
          onSelect={openCase}
          caseNoun={extras.case_noun_plural}
        />
        <div className="cd-controls">
          <div className="cd-sliders">
            <h6>{w.thresholds}</h6>
            <label>
              <span>
                {w.denyAt} <b>{pct(denyAt)}</b>
              </span>
              <input
                type="range"
                min={0.05}
                max={0.95}
                step={0.01}
                value={denyAt}
                style={{ accentColor: palette.deny }}
                onChange={(e) => setDenyAt(Math.min(Number(e.target.value), approveAt - 0.05))}
              />
            </label>
            <label>
              <span>
                {w.approveAt} <b>{pct(approveAt)}</b>
              </span>
              <input
                type="range"
                min={0.05}
                max={0.95}
                step={0.01}
                value={approveAt}
                style={{ accentColor: palette.approve }}
                onChange={(e) => setApproveAt(Math.max(Number(e.target.value), denyAt + 0.05))}
              />
            </label>
            <button className="cd-link" onClick={() => (setApproveAt(basePolicy.approve_at), setDenyAt(basePolicy.deny_at))}>
              ↺ {pct(basePolicy.deny_at)} / {pct(basePolicy.approve_at)}
            </button>
            <label className="cd-toggle">
              <input type="checkbox" checked={revealed} onChange={(e) => setRevealed(e.target.checked)} /> {w.revealReal}
            </label>
          </div>
          <div className="cd-kpis">
            <div className="cd-kpi">
              <b>{pct(stats.automatedShare)}</b>
              <span>{w.automated}</span>
              <small>
                {stats.automated} / {stats.reachingModel}
              </small>
            </div>
            <div className="cd-kpi">
              <b style={{ color: stats.automatedErrorRate > 0.1 ? palette.deny : undefined }}>{pct(stats.automatedErrorRate, 1)}</b>
              <span>{w.errors}</span>
              <small>
                {stats.automatedErrors} / {stats.automated}
              </small>
            </div>
            <div className="cd-kpi">
              <b>{stats.byTray.review.n}</b>
              <span>{w.manual}</span>
              <small>
                {stats.reviewGrantRate === null ? "" : `${w.realGrant}: ${pct(stats.reviewGrantRate)}`}
                {stats.priorityReview ? ` · ${stats.priorityReview} ${w.priority.toLowerCase()}` : ""}
              </small>
            </div>
            <div className="cd-kpi">
              <b>{stoppedTotal}</b>
              <span>{w.stopped}</span>
              <small>
                {stats.byTray.request_info.n} {TRAY_GLYPHS.request_info} · {stats.byTray.reject.n} {TRAY_GLYPHS.reject}
              </small>
            </div>
          </div>
        </div>
      </section>

      <section className="cd-card cd-bench" ref={workbench}>
        <div className="cd-modes" role="tablist">
          {(
            [
              ["portfolio", w.portfolio],
              ["fresh", w.fresh],
              ["scan", w.scan],
              ...(documentTool ? ([["normativa", `📚 ${documentTool.label}`]] as const) : []),
            ] as const
          ).map(([key, label]) => (
            <button key={key} role="tab" aria-selected={view === key} className={view === key ? "active" : ""} onClick={() => setView(key)}>
              {label}
            </button>
          ))}
        </div>

        {view === "portfolio" && (
          <div className="cd-picker">
            <div className="cd-trays">
              {TRAYS.map((t) => (
                <button key={t} className="cd-chip-btn" style={{ borderColor: palette[t] }} onClick={() => openFromTray(t)} title={w.open}>
                  <span style={{ color: palette[t] }}>{TRAY_GLYPHS[t]}</span> {trayLabels[t]} <b>{stats.byTray[t].n}</b>
                </button>
              ))}
            </div>
            <div className="cd-search">
              <input
                type="search"
                value={query}
                placeholder={`${w.open}: ${extras.case_noun}…`}
                onChange={(e) => setQuery(e.target.value)}
              />
              {matches.length > 0 && (
                <ul>
                  {matches.map((c) => (
                    <li key={c.id}>
                      <button onClick={() => (openCase(c.id), setQuery(""))}>
                        <b>{c.id}</b> {c.name}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}

        {view === "fresh" && (
          <div className="cd-picker">
            <div className="cd-personas">
              <button className={`cd-persona${freshLabel === null ? " active" : ""}`} onClick={blank}>
                <b>{w.fresh}</b>
                <small>—</small>
              </button>
              {extras.personas.map((p, i) => (
                <button key={p.label} className={`cd-persona${freshLabel === p.label ? " active" : ""}`} onClick={() => loadPersona(i)}>
                  <b>{p.label}</b>
                  {p.description && <small>{p.description}</small>}
                </button>
              ))}
            </div>
          </div>
        )}

        {view === "scan" && (
          <div className="cd-picker">
            <div className="cd-personas">
              {extras.sample_uploads.map((s) => (
                <button key={s.file} className={`cd-persona${scan.fileName === s.file ? " active" : ""}`} onClick={() => void readSample(s.file)}>
                  <b>📄 {s.label}</b>
                  {s.description && <small>{s.description}</small>}
                </button>
              ))}
              <label className="cd-persona cd-upload">
                <b>⬆ …</b>
                <small>PDF / PNG / JPG</small>
                <input
                  type="file"
                  accept=".pdf,image/*"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void readFile(file);
                    e.target.value = "";
                  }}
                />
              </label>
            </div>
            {scan.phase !== "idle" && (
              <ol className="cd-pipeline">
                {(
                  [
                    ["ocr", `1 · ${w.ocr}`],
                    ["llm", `2 · ${w.llm}`],
                    ["reading", `3 · ${w.casilla}s`],
                  ] as const
                ).map(([phase, label], i) => {
                  const order = ["ocr", "llm", "reading", "done"];
                  const at = scan.phase === "error" ? -1 : order.indexOf(scan.phase);
                  const state = at > i ? "done" : at === i ? "now" : "todo";
                  return (
                    <li key={phase} className={`cd-step cd-step--${state}`}>
                      {state === "done" ? "✓" : state === "now" ? <span className="cd-spinner" /> : "○"} {label}
                    </li>
                  );
                })}
                {scan.result && <li className="cd-step-note">{Object.keys(scan.result.fields).length} / {boxOrder.length}</li>}
                {scan.error && <li className="cd-step-error">{scan.error}</li>}
              </ol>
            )}
          </div>
        )}

        {view === "normativa" && documentTool ? (
          <NormativaDesk scenario={scenario} documentTool={documentTool} locale={extras.locale} accessToken={accessToken} focus={lawFocus} />
        ) : sheet ? (
          <div className="cd-sheet-grid">
            <CaseSheet
              {...panelProps}
              state={sheet}
              editable={view !== "portfolio" && (view !== "scan" || scan.phase === "done")}
              onChange={edit}
              extraction={extraction}
              stampKey={stampKey}
            />
            <DecisionPanel {...panelProps} state={sheet} stampKey={stampKey} onLegalBasis={documentTool ? openLegalBasis : undefined} />
          </div>
        ) : (
          <div className="cd-empty cd-empty--inline">{view === "scan" ? (scan.phase === "idle" ? "📄" : "") : w.noCase}</div>
        )}
        {scoreError && <div className="cd-error">{scoreError}</div>}
      </section>

      {equity.length > 0 && (
        <section className="cd-card cd-equity">
          <h4>{w.equity}</h4>
          <p className="cd-note">{w.equityNote}</p>
          <div className="cd-equity-grid">
            {equity.map(({ column, rows }) => (
              <div key={column} className="cd-equity-col">
                <h6>{column}</h6>
                {rows.map((row) => (
                  <div key={row.group} className="cd-equity-row">
                    <span className="cd-equity-name">
                      {row.group} <small>n={row.n}</small>
                    </span>
                    <span className="cd-stack" title={`${trayLabels.approve} ${pct(row.approveRate)} · ${trayLabels.review} ${pct(row.reviewRate)} · ${trayLabels.deny} ${pct(row.denyRate)}`}>
                      <i style={{ width: `${row.approveRate * 100}%`, background: palette.approve }} />
                      <i style={{ width: `${row.reviewRate * 100}%`, background: palette.review }} />
                      <i style={{ width: `${row.denyRate * 100}%`, background: palette.deny }} />
                    </span>
                    <span className="cd-equity-num">
                      {pct(row.approveRate)} <small>{TRAY_GLYPHS.approve}</small> · <small>{w.real}</small> {pct(row.grantedRate)}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
