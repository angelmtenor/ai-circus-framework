import type { CaseDeskExtra, Decision, DecisionPolicy, OutOfFoldRow, RuleOutcome } from "./apiClient";
import type { Record_ } from "./predictUtils";
import type { SurfaceMode } from "./riskPalette";

/** One application of the portfolio: its boxes, the real resolution, the rules' verdict
 * (computed server-side) and the honest model score (cross-fitted: from a model that
 * never saw this row's label — null for a row the rules stopped, which is never trained on). */
export type DeskCase = {
  id: string;
  name: string;
  record: Record_;
  /** Audit-only columns (e.g. sex, nationality) — never model inputs. */
  audit: Record<string, string>;
  actual: boolean; // the real resolution: aid granted
  gate: RuleOutcome | null;
  fired: string[];
  probability: number | null;
  contributions: Record<string, number> | null;
};

/**
 * Colours of the desk, validated with the dataviz palette validator (all-pairs CVD):
 * grant / review / deny are the reference palette's first three hues (green, blue,
 * orange); the two trays the rules stop are recessive neutrals — they are context, not
 * a category — and every tray also carries its glyph and label, so colour is never alone.
 */
export const DESK_PALETTE: Record<SurfaceMode, Record<Tray, string>> = {
  dark: { approve: "#199e70", review: "#3987e5", deny: "#d95926", request_info: "#7b879c", reject: "#566074" },
  light: { approve: "#1baf7a", review: "#2a78d6", deny: "#eb6834", request_info: "#9aa6b6", reject: "#6f7d90" },
};
export const TRAY_GLYPHS: Record<Tray, string> = { approve: "✓", review: "◐", deny: "✕", request_info: "◌", reject: "⊘" };

/** The five trays an application can end in. The first two are the rules' (before the
 * model); the last three are the model's, split by the decision policy. */
export type Tray = "request_info" | "reject" | "approve" | "review" | "deny";
export const TRAYS: Tray[] = ["request_info", "reject", "approve", "review", "deny"];

/** Mirrors ai_circus_shared.business_rules.decide — needed client-side because the
 * thresholds are sliders, and every application is re-sorted on each move. */
export function decide(policy: Pick<DecisionPolicy, "approve_at" | "deny_at">, c: DeskCase): Decision {
  if (c.gate !== null) return c.gate;
  const p = c.probability ?? 0.5;
  if (p >= policy.approve_at) return "approve";
  if (p <= policy.deny_at) return "deny";
  return "review";
}

/** A review gate (e.g. special protection) sits in the review tray whatever the score. */
export function trayOf(policy: Pick<DecisionPolicy, "approve_at" | "deny_at">, c: DeskCase): Tray {
  return decide(policy, c) as Tray;
}

export type TrayStats = { n: number; granted: number };

export type DeskStats = {
  total: number;
  byTray: Record<Tray, TrayStats>;
  reachingModel: number;
  automated: number; // approve + deny trays
  automatedShare: number; // of the applications that reach the model
  /** Real resolution contradicting the proposal, inside the automated trays. */
  automatedErrors: number;
  automatedErrorRate: number;
  reviewGrantRate: number | null; // how often the manual-review tray was really granted
  priorityReview: number; // review tray members forced there by a review gate
};

export function deskStats(cases: DeskCase[], policy: Pick<DecisionPolicy, "approve_at" | "deny_at">): DeskStats {
  const byTray = Object.fromEntries(TRAYS.map((t) => [t, { n: 0, granted: 0 }])) as Record<Tray, TrayStats>;
  let priority = 0;
  for (const c of cases) {
    const tray = trayOf(policy, c);
    byTray[tray].n += 1;
    if (c.actual) byTray[tray].granted += 1;
    if (tray === "review" && c.gate === "review") priority += 1;
  }
  const automated = byTray.approve.n + byTray.deny.n;
  const errors = (byTray.approve.n - byTray.approve.granted) + byTray.deny.granted;
  const reachingModel = automated + byTray.review.n;
  return {
    total: cases.length,
    byTray,
    reachingModel,
    automated,
    automatedShare: reachingModel ? automated / reachingModel : 0,
    automatedErrors: errors,
    automatedErrorRate: automated ? errors / automated : 0,
    reviewGrantRate: byTray.review.n ? byTray.review.granted / byTray.review.n : null,
    priorityReview: priority,
  };
}

export type EquityRow = {
  group: string;
  n: number;
  grantedRate: number; // real resolutions
  meanProbability: number; // honest model score
  approveRate: number;
  reviewRate: number;
  denyRate: number;
};

/** Outcome rates per group of an audit column, over the applications that reach the model. */
export function equityRows(cases: DeskCase[], column: string, policy: Pick<DecisionPolicy, "approve_at" | "deny_at">): EquityRow[] {
  const groups = new Map<string, DeskCase[]>();
  for (const c of cases) {
    if (c.gate === "reject" || c.gate === "request_info" || c.probability === null) continue;
    const key = c.audit[column] ?? "—";
    groups.set(key, [...(groups.get(key) ?? []), c]);
  }
  return [...groups.entries()]
    .map(([group, members]) => {
      const n = members.length;
      const trays = members.map((c) => trayOf(policy, c));
      const share = (t: Tray) => trays.filter((x) => x === t).length / n;
      return {
        group,
        n,
        grantedRate: members.filter((c) => c.actual).length / n,
        meanProbability: members.reduce((s, c) => s + (c.probability ?? 0), 0) / n,
        approveRate: share("approve"),
        reviewRate: share("review"),
        denyRate: share("deny"),
      };
    })
    .sort((a, b) => b.n - a.n);
}

/** Boxes the desk fills itself (e.g. income per head in IPREM units) — recomputed from
 * their inputs so a form can never carry a ratio that contradicts its own boxes. */
export function withComputedFields(record: Record_, computed: CaseDeskExtra["computed_fields"]): Record_ {
  const next = { ...record };
  for (const c of computed) {
    const numerator = Number(next[c.numerator]);
    const denominator = Number(next[c.denominator]);
    if (Number.isFinite(numerator) && Number.isFinite(denominator) && denominator > 0) {
      next[c.field] = Number((numerator / denominator / c.divisor).toFixed(c.decimals));
    }
  }
  return next;
}

export function joinCases(
  rows: Record<string, string | number | null>[],
  idColumn: string,
  nameColumn: string | null | undefined,
  columns: string[],
  auditColumns: string[],
  target: string,
  gates: Record<string, RuleOutcome | null> | null | undefined,
  fired: Record<string, string[]> | null | undefined,
  oof: Map<string, OutOfFoldRow>,
): DeskCase[] {
  return rows.map((row) => {
    const id = String(row[idColumn]);
    const score = oof.get(id);
    const record: Record_ = {};
    for (const column of columns) {
      const value = row[column];
      if (value !== null && value !== undefined) record[column] = value;
    }
    return {
      id,
      name: nameColumn ? String(row[nameColumn] ?? id) : id,
      record,
      audit: Object.fromEntries(auditColumns.map((c) => [c, String(row[c] ?? "—")])),
      actual: Number(row[target]) === 1,
      gate: gates?.[id] ?? null,
      fired: fired?.[id] ?? [],
      probability: score?.probability ?? null,
      contributions: score?.contributions ?? null,
    };
  });
}

// --- es-ES formatting (the desk's wording is the scenario's; numbers follow its locale) ---

export function formatNumber(value: number, locale: "en" | "es", digits = 0): string {
  return value.toLocaleString(locale === "es" ? "es-ES" : "en-GB", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function pct(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

/** Fixed UI wording of the desk by locale (everything scenario-specific comes from the YAML). */
export const WORDS = {
  es: {
    registry: "Registro",
    rules: "Reglas de negocio",
    model: "Modelo",
    casilla: "Casilla",
    heatTowardApprove: "empuja hacia conceder",
    heatTowardDeny: "empuja hacia denegar",
    notApplied: "Modelo no aplicado",
    weighty: "Casillas que más pesan",
    toReview: "Casillas a revisar",
    probability: "Probabilidad de concesión",
    honest: "Puntuación de un modelo que no vio este expediente",
    live: "Puntuación del modelo desplegado",
    champion: "TF-IDF + LightGBM (desplegado)",
    stopped: "Paradas por las reglas",
    thresholds: "Umbrales de decisión",
    approveAt: "Conceder a partir de",
    denyAt: "Denegar hasta",
    automated: "Resueltas sin revisión",
    errors: "Error en lo automático",
    manual: "A revisión manual",
    realGrant: "Concedidas en realidad",
    real: "real",
    decides: "Resuelve",
    revealReal: "Mostrar la resolución real",
    portfolio: "Cartera",
    fresh: "Nueva solicitud",
    scan: "Registrar documento",
    equity: "Equidad",
    equityNote: "Sexo y nacionalidad nunca entran en el modelo: aquí solo se auditan los resultados.",
    noCase: "Elige una solicitud del circuito para abrirla.",
    open: "Abrir expediente",
    priority: "Prioritario",
    notFound: "No leído",
    unverified: "Sin verificar",
    reading: "Leyendo el documento…",
    ocr: "OCR",
    llm: "Extracción",
    pass: "Cumple",
    fail: "No cumple",
    na: "No aplica",
  },
  en: {
    registry: "Registry",
    rules: "Business rules",
    model: "Model",
    casilla: "Box",
    heatTowardApprove: "pushes towards granting",
    heatTowardDeny: "pushes towards denying",
    notApplied: "Model not applied",
    weighty: "Boxes that weigh most",
    toReview: "Boxes to review",
    probability: "Probability of approval",
    honest: "Score from a model that never saw this case",
    live: "Deployed model score",
    champion: "TF-IDF + LightGBM (deployed)",
    stopped: "Stopped by the rules",
    thresholds: "Decision thresholds",
    approveAt: "Approve from",
    denyAt: "Deny up to",
    automated: "Resolved without review",
    errors: "Error in the automated trays",
    manual: "Sent to manual review",
    realGrant: "Really granted",
    real: "actual",
    decides: "Decided by",
    revealReal: "Show the real resolution",
    portfolio: "Portfolio",
    fresh: "New application",
    scan: "Register a document",
    equity: "Equity",
    equityNote: "Sex and nationality never enter the model: outcomes are only audited here.",
    noCase: "Pick an application in the circuit to open it.",
    open: "Open case",
    priority: "Priority",
    notFound: "Not read",
    unverified: "Unverified",
    reading: "Reading the document…",
    ocr: "OCR",
    llm: "Extraction",
    pass: "Pass",
    fail: "Fail",
    na: "Not applicable",
  },
} as const;
