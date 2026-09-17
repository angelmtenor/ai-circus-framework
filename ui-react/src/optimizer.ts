/**
 * The recipe optimizer's engine — pure functions, no React, no fetch: candidate
 * generation, the (fictional, YAML-declared) economics every candidate is scored
 * with, the two-round search itself, and the live-line stepper. ProcessOptimizerView
 * is the only consumer; it supplies the one side effect (`PredictFn`, the scenario's
 * real, unmodified `/predict/{slug}` batched over every candidate) and renders.
 *
 * Everything here is driven by `ProcessOptimizerExtra` (see apiClient.ts, mirroring
 * libs/shared's scenario_schema.py) plus the scenario's feature_schema — no
 * scenario-specific knowledge, so any regression scenario with a physical target
 * and a few numeric setpoints can opt in from YAML alone.
 */
import type { FeatureSpec, PredictionResult, ProcessOptimizerExtra } from "./apiClient";
import type { Record_ } from "./predictUtils";

export type Objective = "profit" | "throughput" | "quality";

export const OBJECTIVES: { id: Objective; label: string; hint: string }[] = [
  { id: "profit", label: "Max profit rate", hint: "margin per unit × units per hour — values speed and quality together" },
  { id: "throughput", label: "Max throughput", hint: "most units per hour that still meet the spec at the tolerated reject rate" },
  { id: "quality", label: "Best quality", hint: "lowest predicted target, whatever it costs" },
];

export const REJECT_TOLERANCES = [0.02, 0.05, 0.1, 0.25];

// Below this share of the prediction, the 90% interval's implied scatter is
// treated as understated — LightGBM's quantile models are trained separately from
// the point model and can cross it (seen on the real cnc_surface_finish model:
// ~25% of candidates' point predictions fell outside their own interval), and a
// physical measurement always has some repeatability noise anyway (10% is a
// typical surface-roughness gauge repeatability).
const SIGMA_FLOOR_FRACTION = 0.1;
const Z_90 = 1.645; // ±z for a 5%/95% quantile pair

export type UnitEconomics = {
  rate: number; // processing rate, e.g. material removal rate
  cutMinutes: number; // minutes actually processing per unit
  cycleMinutes: number; // fixed + cutMinutes
  unitsPerHour: number;
  machineCost: number; // per unit
  agingRate: number; // tool aging multiplier vs the reference setpoint (1 without tool_life)
  toolCost: number; // per unit: amortized consumable + changeover downtime (0 without tool_life)
  rejectProbability: number; // model's own P(target outside spec)
  expectedRejectCost: number; // per unit
  margin: number; // per unit
  profitPerHour: number;
  batchProfit: number;
  batchHours: number;
};

export type Scored = {
  record: Record_; // the full record scored (fixed context + recipe)
  recipe: Record<string, number>; // just the controllable knobs
  prediction: number; // point prediction, floored at 0 (a physical quantity)
  lower: number | null;
  upper: number | null;
  sigma: number;
  contributions: Record<string, number>;
  econ: UnitEconomics;
  feasible: boolean; // rejectProbability <= the tolerated rate
  round: number; // 0 = the current recipe itself, 1 = coarse, 2 = refinement
};

export type PredictFn = (records: Record_[]) => Promise<PredictionResult[]>;

export type OptimizeSettings = {
  spec: number;
  objective: Objective;
  maxRejectRate: number;
};

export type OptimizeResult = {
  best: Scored;
  noneFeasible: boolean; // best is the least-violating candidate, not an in-spec one
  candidates: Scored[]; // every candidate scored, both rounds, for the trade-off plot
  elapsedMs: number;
};

export type OptimizeProgress = { stage: string; evaluated: number; total: number };

// ── numerics ────────────────────────────────────────────────────────────────────

/** Standard normal CDF (Abramowitz & Stegun 26.2.17, |error| < 7.5e-8). */
export function normalCdf(z: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const poly = t * (0.31938153 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  const tail = Math.exp(-(z * z) / 2) * poly * 0.3989422804014327;
  return z >= 0 ? 1 - tail : tail;
}

/** Deterministic PRNG (mulberry32) so a re-run with the same inputs explores the
 * same candidate positions — the recommendation is then stable across clicks, which
 * matters for a demo; only the model's predictions (the context/wear) move it. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

/** Decimal places that a feature's slider step actually resolves (0.01 → 2, 5 → 0). */
export function stepDecimals(spec: FeatureSpec): number {
  if (spec.type !== "numeric") return 0;
  const step = spec.step ?? 1;
  return step >= 1 ? 0 : Math.min(6, Math.ceil(-Math.log10(step)));
}

/** Snap a continuous sample onto the feature's real slider grid (step + bounds) so
 * every recommended setpoint is one an operator could actually dial in. */
export function snap(value: number, spec: FeatureSpec): number {
  if (spec.type !== "numeric") return value;
  const step = spec.step ?? 1;
  const snapped = spec.min + Math.round((value - spec.min) / step) * step;
  return Number(clamp(snapped, spec.min, spec.max).toFixed(stepDecimals(spec)));
}

/** σ implied by the model's 90% interval, floored at SIGMA_FLOOR_FRACTION of |μ|. */
export function impliedSigma(mu: number, lower: number | null, upper: number | null): number {
  const fromInterval = lower !== null && upper !== null ? Math.max(0, upper - lower) / (2 * Z_90) : 0;
  return Math.max(fromInterval, SIGMA_FLOOR_FRACTION * Math.abs(mu), 1e-6);
}

/** P(target misses the spec) under a normal(μ, σ) — the model's own uncertainty,
 * not a hard threshold, so a recipe sitting right on the limit is correctly scored
 * as a coin-flip rather than "in spec". */
export function rejectProbability(mu: number, sigma: number, spec: number, objective: "minimize" | "maximize"): number {
  const z = (spec - mu) / sigma;
  return objective === "minimize" ? 1 - normalCdf(z) : normalCdf(z);
}

// ── economics ───────────────────────────────────────────────────────────────────

export function processingRate(record: Record_, extras: ProcessOptimizerExtra): number {
  const { rate_features, rate_scale } = extras.economics.cycle_time;
  return rate_features.reduce((acc, f) => acc * Number(record[f]), rate_scale);
}

/** Tool aging multiplier at this recipe's setpoint vs the reference — Taylor's
 * V·Tⁿ = C rearranged: (v / v_ref)^(1/n). 1 with no tool_life block. */
export function agingRate(record: Record_, extras: ProcessOptimizerExtra): number {
  const tl = extras.economics.tool_life;
  if (!tl) return 1;
  const v = Math.max(1e-9, Number(record[tl.feature]));
  return Math.pow(v / tl.reference_value, 1 / tl.taylor_n);
}

export function unitEconomics(record: Record_, mu: number, sigma: number, extras: ProcessOptimizerExtra, spec: number): UnitEconomics {
  const e = extras.economics;
  const ct = e.cycle_time;
  const rate = processingRate(record, extras);
  const cutMinutes = ct.rate_features.length > 0 ? ct.work_per_unit / Math.max(rate, 1e-9) : 0;
  const cycleMinutes = ct.fixed_minutes + cutMinutes;
  const unitsPerHour = 60 / Math.max(cycleMinutes, 1e-9);
  const machineCost = (e.machine_rate_per_hour / 60) * cycleMinutes;
  const aging = agingRate(record, extras);
  let toolCost = 0;
  if (e.tool_life) {
    // Per unit: the share of one tool life this unit consumes, times what a change
    // costs (the consumable itself plus the machine standing idle while it happens).
    const lifeShare = (cutMinutes * aging) / e.tool_life.reference_life_minutes;
    toolCost = lifeShare * (e.tool_life.cost_per_edge + (e.tool_life.change_minutes * e.machine_rate_per_hour) / 60);
  }
  const rejectProb = rejectProbability(mu, sigma, spec, extras.objective);
  const expectedRejectCost = rejectProb * e.reject_cost;
  const margin = e.unit_value - machineCost - toolCost - expectedRejectCost;
  const profitPerHour = margin * unitsPerHour;
  return {
    rate,
    cutMinutes,
    cycleMinutes,
    unitsPerHour,
    machineCost,
    agingRate: aging,
    toolCost,
    rejectProbability: rejectProb,
    expectedRejectCost,
    margin,
    profitPerHour,
    batchProfit: margin * e.batch_size,
    batchHours: (cycleMinutes * e.batch_size) / 60,
  };
}

export function scoreRecord(record: Record_, result: PredictionResult, extras: ProcessOptimizerExtra, settings: OptimizeSettings, round: number): Scored {
  // Tree models can extrapolate a hair below zero for a quantity that can't be —
  // floor it, for both the economics and what the user sees.
  const prediction = Math.max(0, result.prediction);
  const sigma = impliedSigma(prediction, result.prediction_lower, result.prediction_upper);
  const econ = unitEconomics(record, prediction, sigma, extras, settings.spec);
  const recipe: Record<string, number> = {};
  for (const f of extras.controllable) recipe[f] = Number(record[f]);
  return {
    record,
    recipe,
    prediction,
    lower: result.prediction_lower,
    upper: result.prediction_upper,
    sigma,
    contributions: result.contributions,
    econ,
    feasible: econ.rejectProbability <= settings.maxRejectRate,
    round,
  };
}

export function objectiveValue(s: Scored, objective: Objective, direction: "minimize" | "maximize"): number {
  if (objective === "profit") return s.econ.profitPerHour;
  if (objective === "throughput") return s.econ.unitsPerHour;
  return direction === "minimize" ? -s.prediction : s.prediction;
}

// ── candidate search ────────────────────────────────────────────────────────────

const COARSE_CANDIDATES = 120;
const REFINE_CANDIDATES = 80;
const REFINE_PARENTS = 3;
const REFINE_RADIUS_FRACTION = 0.15; // of each knob's full span
const SEED = 42;

function sampleKnob(f: string, extras: ProcessOptimizerExtra, schema: Record<string, FeatureSpec>, rng: () => number, center?: number): number {
  const spec = schema[f];
  if (spec.type !== "numeric") return Number(center ?? 0);
  const discrete = extras.discrete_values[f];
  if (discrete && discrete.length > 0) {
    // Refinement keeps the parent's discrete choice most of the time — the point of
    // that round is to fine-tune the continuous knobs around it, not to re-roll it.
    if (center !== undefined && rng() < 0.7) return center;
    return discrete[Math.floor(rng() * discrete.length)];
  }
  const span = spec.max - spec.min;
  if (center === undefined) return snap(spec.min + rng() * span, spec);
  const radius = span * REFINE_RADIUS_FRACTION;
  return snap(center + (rng() * 2 - 1) * radius, spec);
}

function sampleRecipe(extras: ProcessOptimizerExtra, schema: Record<string, FeatureSpec>, rng: () => number, parent?: Record<string, number>): Record<string, number> {
  const recipe: Record<string, number> = {};
  for (const f of extras.controllable) recipe[f] = sampleKnob(f, extras, schema, rng, parent?.[f]);
  return recipe;
}

function pickBest(candidates: Scored[], settings: OptimizeSettings, direction: "minimize" | "maximize"): { best: Scored; noneFeasible: boolean } {
  const feasible = candidates.filter((c) => c.feasible);
  if (feasible.length > 0) {
    const best = feasible.reduce((a, b) => (objectiveValue(b, settings.objective, direction) > objectiveValue(a, settings.objective, direction) ? b : a));
    return { best, noneFeasible: false };
  }
  const best = candidates.reduce((a, b) => (b.econ.rejectProbability < a.econ.rejectProbability ? b : a));
  return { best, noneFeasible: true };
}

/**
 * Two-round derivative-free search over the controllable knobs, every candidate
 * scored by the real model in one batched call per round: a coarse uniform sample
 * over the full recipe box (plus the current recipe itself, so "leave it alone" is
 * always on the table), then a refinement sample in a shrunken box around the top
 * few feasible candidates. Simple on purpose — ~200 evaluations in ~1.5 s against
 * the real service is what makes this a live "click and watch" demo, and the
 * trade-off plot shows every candidate so the choice is auditable, not a black box.
 */
export async function optimize(
  context: Record_,
  extras: ProcessOptimizerExtra,
  schema: Record<string, FeatureSpec>,
  settings: OptimizeSettings,
  predictFn: PredictFn,
  onProgress?: (p: OptimizeProgress) => void,
): Promise<OptimizeResult> {
  const started = performance.now();
  const rng = mulberry32(SEED);
  const total = 1 + COARSE_CANDIDATES + REFINE_CANDIDATES;
  const currentRecipe: Record<string, number> = {};
  for (const f of extras.controllable) currentRecipe[f] = Number(context[f]);

  onProgress?.({ stage: `Round 1 — scoring ${COARSE_CANDIDATES} candidate recipes across the full range…`, evaluated: 0, total });
  const round1Recipes = [currentRecipe, ...Array.from({ length: COARSE_CANDIDATES }, () => sampleRecipe(extras, schema, rng))];
  const round1Records = round1Recipes.map((r) => ({ ...context, ...r }));
  const round1Results = await predictFn(round1Records);
  const round1 = round1Results.map((res, i) => scoreRecord(round1Records[i], res, extras, settings, i === 0 ? 0 : 1));

  const ranked = round1
    .filter((c) => c.feasible)
    .sort((a, b) => objectiveValue(b, settings.objective, extras.objective) - objectiveValue(a, settings.objective, extras.objective));
  const parents = (ranked.length > 0 ? ranked : [pickBest(round1, settings, extras.objective).best]).slice(0, REFINE_PARENTS);

  onProgress?.({ stage: `Round 2 — refining ${REFINE_CANDIDATES} variations around the ${parents.length} best…`, evaluated: round1.length, total });
  const round2Recipes = Array.from({ length: REFINE_CANDIDATES }, (_, i) => sampleRecipe(extras, schema, rng, parents[i % parents.length].recipe));
  const round2Records = round2Recipes.map((r) => ({ ...context, ...r }));
  const round2Results = await predictFn(round2Records);
  const round2 = round2Results.map((res, i) => scoreRecord(round2Records[i], res, extras, settings, 2));

  const candidates = [...round1, ...round2];
  const { best, noneFeasible } = pickBest(candidates, settings, extras.objective);
  onProgress?.({ stage: "Done", evaluated: candidates.length, total });
  return { best, noneFeasible, candidates, elapsedMs: performance.now() - started };
}

// ── live line simulation ────────────────────────────────────────────────────────

export type LineEvent = { t: number; kind: "recipe" | "tool" | "start"; text: string };

export type LinePoint = { t: number; prediction: number; lower: number; upper: number; rejectProbability: number; profit: number };

export type LineState = {
  id: "optimized" | "baseline";
  label: string;
  recipe: Record<string, number>;
  wear: number | null; // current wear_feature value, null without one
  freshEdge: boolean; // set by the tick that changed a spent tool — a cue to re-optimize on it
  downtimeLeft: number; // minutes of tool change still to serve before producing again
  carry: number; // fractional unit carried into the next tick
  units: number;
  rejects: number;
  toolChanges: number;
  recipeChanges: number;
  profit: number;
  last: Scored | null;
  history: LinePoint[];
  events: LineEvent[];
};

export function makeLine(id: LineState["id"], label: string, recipe: Record<string, number>, wear: number | null): LineState {
  return { id, label, recipe, wear, freshEdge: false, downtimeLeft: 0, carry: 0, units: 0, rejects: 0, toolChanges: 0, recipeChanges: 0, profit: 0, last: null, history: [], events: [] };
}

export function lineRecord(line: LineState, context: Record_, extras: ProcessOptimizerExtra): Record_ {
  const record: Record_ = { ...context, ...line.recipe };
  if (extras.wear_feature && line.wear !== null) record[extras.wear_feature] = Number(line.wear.toFixed(2));
  return record;
}

/** Charge a tool change to the line: consumable now, downtime served over the next
 * tick(s), wear back to the feature's minimum (a fresh edge). */
export function changeTool(line: LineState, extras: ProcessOptimizerExtra, schema: Record<string, FeatureSpec>, t: number, why: string): LineState {
  const tl = extras.economics.tool_life;
  const wearSpec = extras.wear_feature ? schema[extras.wear_feature] : undefined;
  return {
    ...line,
    wear: wearSpec?.type === "numeric" ? wearSpec.min : line.wear,
    downtimeLeft: line.downtimeLeft + (tl?.change_minutes ?? 0),
    profit: line.profit - (tl?.cost_per_edge ?? 0),
    toolChanges: line.toolChanges + 1,
    events: [...line.events, { t, kind: "tool", text: why }],
  };
}

/**
 * Advance one line by `minutes` of simulated time given this tick's scored
 * prediction for its current recipe/wear: serve any tool-change downtime first,
 * produce units at the recipe's cycle time, roll each unit's in-spec outcome
 * against the model's own reject probability, charge the machine rate for every
 * elapsed minute (idle or not), and age the tool by real cutting time × the Taylor
 * aging rate so a spent tool (wear at the feature's max — the edge of what the model
 * has ever seen) forces a change.
 */
export function stepLine(line: LineState, scored: Scored, extras: ProcessOptimizerExtra, schema: Record<string, FeatureSpec>, t: number, minutes: number, rng: () => number): LineState {
  const e = extras.economics;
  const downtimeServed = Math.min(line.downtimeLeft, minutes);
  const available = minutes - downtimeServed;
  const unitsFloat = available / Math.max(scored.econ.cycleMinutes, 1e-9) + line.carry;
  const produced = Math.floor(unitsFloat);
  let rejected = 0;
  for (let i = 0; i < produced; i++) if (rng() < scored.econ.rejectProbability) rejected++;

  const profit = line.profit + produced * e.unit_value - rejected * e.reject_cost - (minutes * e.machine_rate_per_hour) / 60;

  let next: LineState = {
    ...line,
    freshEdge: false,
    downtimeLeft: line.downtimeLeft - downtimeServed,
    carry: unitsFloat - produced,
    units: line.units + produced,
    rejects: line.rejects + rejected,
    profit,
    last: scored,
    history: [
      ...line.history,
      {
        t,
        prediction: scored.prediction,
        lower: Math.max(0, scored.prediction - Z_90 * scored.sigma),
        upper: scored.prediction + Z_90 * scored.sigma,
        rejectProbability: scored.econ.rejectProbability,
        profit,
      },
    ],
  };

  const wearSpec = extras.wear_feature ? schema[extras.wear_feature] : undefined;
  if (extras.wear_feature && wearSpec?.type === "numeric" && e.tool_life && next.wear !== null) {
    const span = wearSpec.max - wearSpec.min;
    const aged = next.wear + (produced * scored.econ.cutMinutes * scored.econ.agingRate * span) / e.tool_life.reference_life_minutes;
    if (aged >= wearSpec.max) {
      next = { ...changeTool({ ...next, wear: wearSpec.max }, extras, schema, t, "Tool spent — changed on schedule"), freshEdge: true };
    } else {
      next = { ...next, wear: aged };
    }
  }
  return next;
}

export function formatRecipeDelta(from: Record<string, number>, to: Record<string, number>, labels: (f: string) => string): string {
  const parts: string[] = [];
  for (const f of Object.keys(to)) {
    if (from[f] !== to[f]) parts.push(`${labels(f)} ${from[f]} → ${to[f]}`);
  }
  return parts.length > 0 ? parts.join(", ") : "no change";
}
