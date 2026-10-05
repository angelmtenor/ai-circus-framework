import { useMemo, type ReactNode } from "react";
import type {
  CaseDeskExtra,
  Decision,
  DecisionPolicy,
  DlTokenWeight,
  ExtractedField,
  FeatureSpec,
  PredictionResult,
  RuleOutcome,
  RuleResult,
  ScenarioSummary,
} from "./apiClient";
import { DESK_PALETTE, formatNumber, pct, TRAY_GLYPHS, WORDS, type Tray } from "./caseDeskLogic";
import { explainByFeature, type Record_ } from "./predictUtils";
import type { SurfaceMode } from "./riskPalette";

/** Where the boxes of a scanned application stand while the document is being read. */
export type ExtractionView = {
  fields: Record<string, ExtractedField>;
  rejected: Record<string, string>;
  /** Boxes revealed so far, in sheet order — the rest are still "being read". */
  revealed: number;
  done: boolean;
};

export type SheetState = {
  record: Record_;
  /** Transformed SHAP contributions (OOF for a portfolio case, live otherwise). */
  contributions: Record<string, number> | null;
  probability: number | null;
  /** The deployed model's live score of this record (equals `probability` unless that is cross-fitted). */
  liveProbability: number | null;
  rules: RuleResult[] | null;
  gate: RuleOutcome | null;
  decision: Decision | null;
  tokens: Record<string, DlTokenWeight[]> | null;
  challenger: PredictionResult | null;
  challengerError: string | null;
  scoring: boolean;
  /** "honest" = cross-fitted score of a portfolio case; "live" = the deployed model. */
  scoreSource: "honest" | "live";
  actual: boolean | null;
};

type Common = {
  scenario: ScenarioSummary;
  extras: CaseDeskExtra;
  policy: DecisionPolicy;
  mode: SurfaceMode;
};

const words = (extras: CaseDeskExtra) => WORDS[extras.locale];

function specOf(scenario: ScenarioSummary, column: string): FeatureSpec | undefined {
  return scenario.feature_schema?.[column] ?? scenario.rule_columns?.[column];
}

function displayValue(spec: FeatureSpec, value: number | string | undefined, locale: "en" | "es"): string {
  if (value === undefined || value === "") return "—";
  if (spec.type === "numeric") {
    const n = Number(value);
    const digits = (spec.step ?? 1) < 1 ? 2 : 0;
    const money = spec.label.includes("€");
    return `${formatNumber(n, locale, digits)}${money ? " €" : ""}`;
  }
  return String(value);
}

/** Words coloured by their share of the explained box: green = towards granting, orange = towards denying. */
function Highlighted({ text, tokens, mode }: { text: string; tokens: DlTokenWeight[]; mode: SurfaceMode }) {
  const palette = DESK_PALETTE[mode];
  const max = Math.max(1e-9, ...tokens.map((t) => Math.abs(t.weight ?? 0)));
  const parts: ReactNode[] = [];
  let cursor = 0;
  tokens.forEach((t, i) => {
    if (t.start > cursor) parts.push(<span key={`g${i}`}>{text.slice(cursor, t.start)}</span>);
    const w = t.weight ?? 0;
    const strength = Math.round((Math.abs(w) / max) * 62);
    parts.push(
      <mark
        key={i}
        className="cd-word"
        style={strength > 6 ? { background: `color-mix(in srgb, ${w >= 0 ? palette.approve : palette.deny} ${strength}%, transparent)` } : undefined}
        title={w === 0 ? "" : `${w >= 0 ? "+" : "−"}${Math.abs(w * 100).toFixed(1)} pts`}
      >
        {text.slice(t.start, t.end)}
      </mark>,
    );
    cursor = t.end;
  });
  if (cursor < text.length) parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return <>{parts}</>;
}

function Control({
  spec,
  value,
  onChange,
}: {
  spec: FeatureSpec;
  value: number | string | undefined;
  onChange: (value: number | string) => void;
}) {
  if (spec.type === "text")
    return (
      <textarea
        className="cd-input cd-input--text"
        rows={3}
        maxLength={spec.max_length}
        placeholder={spec.placeholder ?? undefined}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  if (spec.type === "numeric")
    return (
      <input
        className="cd-input"
        type="number"
        min={spec.min}
        max={spec.max}
        step={spec.step ?? 1}
        value={value === undefined ? "" : value}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
      />
    );
  return (
    <select className="cd-input" value={value === undefined ? "" : String(value)} onChange={(e) => onChange(e.target.value)}>
      {value === undefined && <option value="">—</option>}
      {spec.options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

/** The application as an official paper sheet: numbered boxes ("casillas"), each lit by
 * how much it pushed the proposal, text boxes with their words highlighted. */
export function CaseSheet({
  scenario,
  extras,
  mode,
  state,
  editable,
  onChange,
  extraction,
  stampKey,
  policy,
}: Common & {
  state: SheetState;
  editable: boolean;
  onChange?: (field: string, value: number | string) => void;
  extraction?: ExtractionView | null;
  stampKey: string;
}) {
  const w = words(extras);
  const palette = DESK_PALETTE[mode];
  const heat = useMemo(() => {
    const columns = scenario.feature_columns ?? [];
    if (!state.contributions || state.probability === null) return new Map<string, number>();
    return new Map(explainByFeature(columns, state.probability, state.contributions).items.map((i) => [i.feature, i.value]));
  }, [scenario.feature_columns, state.contributions, state.probability]);
  const maxHeat = Math.max(1e-6, ...[...heat.values()].map((v) => Math.abs(v)));
  const computed = new Set(extras.computed_fields.map((c) => c.field));

  let number = 0;
  let readIndex = 0;
  return (
    <article className={`cd-sheet${extraction && !extraction.done ? " cd-sheet--scanning" : ""}`}>
      <header className="cd-sheet-head">
        <div>
          <strong>{extras.authority.toUpperCase()}</strong>
          {extras.authority_unit && <small>{extras.authority_unit}</small>}
        </div>
        {extras.form_code && <span className="cd-sheet-code">{extras.form_code}</span>}
      </header>
      <h4 className="cd-sheet-title">{extras.form_title}</h4>
      {extras.sections.map((section) => (
        <section key={section.title} className="cd-sec">
          <h5>{section.title}</h5>
          <div className="cd-boxes">
            {section.fields.map((column) => {
              const spec = specOf(scenario, column);
              if (!spec) return null;
              number += 1;
              const index = readIndex++;
              const value = state.record[column];
              const read = extraction?.fields[column];
              const visible = !extraction || index < extraction.revealed || extraction.done;
              const reading = !!extraction && !extraction.done && index === extraction.revealed;
              const missing = !!extraction && extraction.done && read === undefined && !computed.has(column) && value === undefined;
              const v = heat.get(column) ?? 0;
              const strength = Math.min(0.5, (Math.abs(v) / maxHeat) * 0.5);
              const tint = Math.abs(v) / maxHeat > 0.04 ? (v >= 0 ? palette.approve : palette.deny) : null;
              const isText = spec.type === "text";
              const tokens = state.tokens?.[column];
              return (
                <div
                  key={column}
                  className={`cd-box${isText ? " cd-box--wide" : ""}${reading ? " cd-box--reading" : ""}${missing ? " cd-box--missing" : ""}${visible ? "" : " cd-box--pending"}`}
                  style={tint ? { background: `color-mix(in srgb, ${tint} ${Math.round(strength * 100)}%, var(--cd-paper))` } : undefined}
                  title={tint ? `${spec.label}: ${v >= 0 ? w.heatTowardApprove : w.heatTowardDeny} (${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pts)` : spec.label}
                >
                  <span className="cd-box-no">{String(number).padStart(2, "0")}</span>
                  <label className="cd-box-label">{spec.label}</label>
                  <div className="cd-box-value">
                    {!visible ? (
                      <span className="cd-pending">…</span>
                    ) : editable && !computed.has(column) ? (
                      <Control spec={spec} value={value} onChange={(val) => onChange?.(column, val)} />
                    ) : isText && typeof value === "string" && tokens ? (
                      <p className="cd-text">
                        <Highlighted text={value} tokens={tokens} mode={mode} />
                      </p>
                    ) : isText ? (
                      <p className="cd-text">{String(value || "—")}</p>
                    ) : (
                      <span className="cd-val">{displayValue(spec, value, extras.locale)}</span>
                    )}
                    {computed.has(column) && <em className="cd-tag">calc.</em>}
                    {visible && read && (
                      <em
                        className={`cd-chip${read.confidence < 0.7 || !read.verified ? " cd-chip--low" : ""}`}
                        title={read.evidence ? `“${read.evidence}”` : undefined}
                      >
                        {read.verified ? "✓" : "?"} {Math.round(read.confidence * 100)}%
                      </em>
                    )}
                    {visible && extraction?.rejected[column] && (
                      <em className="cd-chip cd-chip--low" title={extraction.rejected[column]}>
                        {w.unverified}
                      </em>
                    )}
                    {missing && <em className="cd-chip cd-chip--low">{w.notFound}</em>}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ))}
      <footer className="cd-sheet-foot">
        <span>{extras.note}</span>
        {state.decision && (
          <div key={stampKey} className="cd-stamp" style={{ color: palette[state.decision as Tray], borderColor: palette[state.decision as Tray] }}>
            <span>{TRAY_GLYPHS[state.decision as Tray]}</span> {policy.labels[state.decision]}
          </div>
        )}
      </footer>
    </article>
  );
}

function ProbabilityBar({ p, policy, mode, applied }: { p: number | null; policy: DecisionPolicy; mode: SurfaceMode; applied: boolean }) {
  const palette = DESK_PALETTE[mode];
  if (p === null) return null;
  return (
    <div className={`cd-prob${applied ? "" : " cd-prob--off"}`}>
      <div className="cd-prob-track">
        <i style={{ left: 0, width: `${policy.deny_at * 100}%`, background: palette.deny }} />
        <i style={{ left: `${policy.deny_at * 100}%`, width: `${(policy.approve_at - policy.deny_at) * 100}%`, background: palette.review }} />
        <i style={{ left: `${policy.approve_at * 100}%`, right: 0, background: palette.approve }} />
        <b style={{ left: `${p * 100}%` }} />
      </div>
      <div className="cd-prob-scale">
        <span>0</span>
        <span style={{ left: `${policy.deny_at * 100}%` }}>{pct(policy.deny_at)}</span>
        <span style={{ left: `${policy.approve_at * 100}%` }}>{pct(policy.approve_at)}</span>
        <span>100</span>
      </div>
    </div>
  );
}

/** The verdict next to the sheet: stamp, score against the thresholds, the rules checklist,
 * the boxes that weigh most, and the deployed model against its challenger. */
export function DecisionPanel({
  scenario,
  extras,
  policy,
  mode,
  state,
  stampKey,
  onLegalBasis,
}: Common & { state: SheetState; stampKey: string; onLegalBasis?: (rule: RuleResult) => void }) {
  const w = words(extras);
  const palette = DESK_PALETTE[mode];
  const rules = state.rules ?? [];
  const families = scenario.business_rules?.families ?? [];
  const outcomeLabels = scenario.business_rules?.outcome_labels;
  const gated = state.gate === "reject" || state.gate === "request_info";
  const items = useMemo(() => {
    if (!state.contributions || state.probability === null) return [];
    return explainByFeature(scenario.feature_columns ?? [], state.probability, state.contributions).items.filter((i) => Math.abs(i.value) > 0.004).slice(0, 7);
  }, [scenario.feature_columns, state.contributions, state.probability]);
  const maxItem = Math.max(1e-6, ...items.map((i) => Math.abs(i.value)));
  const decision = state.decision;
  const label = decision ? policy.labels[decision] : null;
  const stoppedRules = rules.filter((r) => r.fired && (r.outcome === "reject" || r.outcome === "request_info"));

  return (
    <aside className="cd-panel">
      {decision && label && (
        <div key={stampKey} className="cd-verdict" style={{ borderColor: palette[decision as Tray], color: palette[decision as Tray] }}>
          <span className="cd-verdict-glyph">{TRAY_GLYPHS[decision as Tray]}</span>
          <div>
            <strong>{label}</strong>
            <small>{gated ? w.notApplied : state.gate === "review" ? `${w.priority} · ${outcomeLabels?.review ?? ""}` : `${w.decides}: ${extras.reviewer_noun}`}</small>
          </div>
        </div>
      )}
      {state.scoring && <div className="cd-scoring"><span className="cd-spinner" /> …</div>}

      {state.probability !== null && (
        <div className="cd-block">
          <h6>
            {w.probability} · <b>{pct(state.probability)}</b>
          </h6>
          <ProbabilityBar p={state.probability} policy={policy} mode={mode} applied={!gated} />
          <small className="cd-note">{gated ? w.notApplied : state.scoreSource === "honest" ? w.honest : w.live}</small>
        </div>
      )}

      {rules.length > 0 && (
        <div className="cd-block">
          <h6>{w.rules}</h6>
          {families.map((family) => {
            const members = rules.filter((r) => r.family === family.key);
            if (members.length === 0) return null;
            return (
              <div key={family.key} className="cd-family">
                <span className="cd-family-title" title={family.description ?? undefined}>{family.label}</span>
                <ul>
                  {members.map((r, i) => {
                    const state_ = !r.applicable ? "na" : r.fired ? "fail" : "pass";
                    const colour = state_ === "pass" ? palette.approve : state_ === "fail" ? (r.outcome === "review" ? palette.review : palette.deny) : "var(--dim)";
                    return (
                      <li key={`${stampKey}-${r.key}`} className={`cd-rule cd-rule--${state_}`} style={{ animationDelay: `${i * 55}ms` }} title={r.legal_basis ?? undefined}>
                        <span className="cd-rule-mark" style={{ color: colour, borderColor: colour }}>
                          {state_ === "pass" ? "✓" : state_ === "fail" ? (r.outcome === "review" ? "◐" : "✕") : "–"}
                        </span>
                        <span>
                          <b>{r.label}</b>
                          {state_ === "fail" && (
                            <small>
                              {r.message}
                              {r.legal_basis ? ` · ${r.legal_basis}` : ""}
                              {r.legal_basis && onLegalBasis && (
                                <button type="button" className="cd-law-link" onClick={() => onLegalBasis(r)}>
                                  📖 {w.seeLaw}
                                </button>
                              )}
                            </small>
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
          {stoppedRules.length === 0 && state.gate === null && <small className="cd-note">{w.pass}</small>}
        </div>
      )}

      {items.length > 0 && !gated && (
        <div className="cd-block">
          <h6>{decision === "review" ? w.toReview : w.weighty}</h6>
          <ul className="cd-drivers">
            {items.map((item) => {
              const spec = specOf(scenario, item.feature);
              const toward = item.value >= 0;
              return (
                <li key={item.feature}>
                  <span className="cd-driver-name">{spec?.label ?? item.feature}</span>
                  <span className="cd-driver-bar">
                    <i
                      style={{
                        background: toward ? palette.approve : palette.deny,
                        width: `${(Math.abs(item.value) / maxItem) * 50}%`,
                        [toward ? "left" : "right"]: "50%",
                      }}
                    />
                  </span>
                  <span className="cd-driver-val">{`${toward ? "+" : "−"}${Math.abs(item.value * 100).toFixed(1)}`}</span>
                </li>
              );
            })}
          </ul>
          <small className="cd-note cd-legend">
            <i style={{ background: palette.approve }} /> {w.heatTowardApprove} <i style={{ background: palette.deny }} /> {w.heatTowardDeny}
          </small>
        </div>
      )}

      {state.challenger && state.probability !== null && (
        <div className="cd-block">
          <h6>{scenario.text_challenger?.label ?? "Challenger"}</h6>
          {[
            { name: w.champion, p: state.liveProbability ?? state.probability },
            { name: scenario.text_challenger?.label ?? "Challenger", p: state.challenger.prediction },
          ].map((row) => (
            <div key={row.name} className="cd-duel">
              <span>{row.name}</span>
              <span className="cd-duel-bar"><i style={{ width: `${row.p * 100}%` }} /></span>
              <b>{pct(row.p)}</b>
            </div>
          ))}
        </div>
      )}
      {state.challengerError && <small className="cd-note">Challenger: {state.challengerError}</small>}
      {state.actual !== null && (
        <small className="cd-note">
          {w.realGrant}: <b>{state.actual ? "✓" : "✕"}</b>
        </small>
      )}
    </aside>
  );
}
