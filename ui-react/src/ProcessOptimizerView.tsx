import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { predict, type FeatureSpec, type ProcessOptimizerExtra, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { BarList, CHART_COLORS, MultiLineChart, StatTile, TradeoffScatter } from "./charts";
import { Icon } from "./Icon";
import { InfoButton } from "./InfoButton";
import { FeatureInput, featureLabel, initialRecord, mapContributions, type Record_ } from "./predictUtils";
import {
  OBJECTIVES,
  REJECT_TOLERANCES,
  changeTool,
  formatRecipeDelta,
  lineRecord,
  makeLine,
  mulberry32,
  optimize,
  scoreRecord,
  stepDecimals,
  stepLine,
  type LineState,
  type Objective,
  type OptimizeProgress,
  type OptimizeResult,
  type OptimizeSettings,
  type Scored,
} from "./optimizer";

const AUTOPILOT_HORIZON_MINUTES = 30; // how far ahead "re-optimize vs change tool" is compared
const AUTOPILOT_COOLDOWN_TICKS = 3; // let an applied recipe settle before intervening again
const FRESH_EDGE_MIN_GAIN = 0.02; // re-optimizing on a fresh tool only applies a recipe at least this much better (profit rate)
const SCORE_DEBOUNCE_MS = 350;
const EVENT_LOG_LENGTH = 8;
const Z_90_DISPLAY = 1.645;
const TRADEOFF_X_SPAN_FACTOR = 3; // trade-off plot shows up to this × the spec; far-out candidates are counted, not drawn

type Lines = { optimized: LineState; baseline: LineState };

function pickRecipe(record: Record_, controllable: string[]): Record<string, number> {
  const recipe: Record<string, number> = {};
  for (const f of controllable) recipe[f] = Number(record[f]);
  return recipe;
}

function money(currency: string, v: number, decimals = 0): string {
  const sign = v < 0 ? "−" : "";
  return `${sign}${currency}${Math.abs(v).toFixed(decimals)}`;
}

function pct(v: number, decimals = 1): string {
  return `${(v * 100).toFixed(decimals)}%`;
}

function riskColor(p: number, tolerated: number): string {
  if (p <= tolerated) return CHART_COLORS.green;
  if (p <= tolerated * 3) return CHART_COLORS.amber;
  return CHART_COLORS.red;
}

function simClock(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `${h} h ${String(m).padStart(2, "0")} min` : `${m} min`;
}

/**
 * Generic 5th workspace tab for any regression `tabular_ml` scenario that sets
 * `ui_extras: {kind: process_optimizer, ...}` (see libs/shared/scenario_schema.py)
 * — the "take action" stage after prediction. cnc_surface_finish is the first user,
 * but nothing here knows what a feed rate is: which knobs are searchable, the spec
 * grades, the (fictional) economics and the tool-life physics all come from the YAML
 * block, and every candidate recipe is scored by the scenario's own, unmodified
 * `/predict/{slug}` (see optimizer.ts for the search and cost model).
 *
 * Three layers, top to bottom: the current recipe and its live prediction/economics
 * (re-scored as you drag a slider), the optimizer's recommendation with a full
 * candidate trade-off plot and an "Apply" button, and a client-side live-line
 * simulation where the tool ages with real cutting time so the model's own
 * predicted drift, an auto-pilot's re-optimizations/tool changes, and a static
 * "old way" baseline play out side by side over a couple of minutes. Nothing here
 * reads real telemetry or writes to a real controller — every control is labelled
 * as a demo simulation.
 */
export function ProcessOptimizerView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as ProcessOptimizerExtra;
  const econ = extras.economics;
  const currency = econ.currency;
  const units = scenario.target_units ?? "";
  const targetLabel = scenario.target_label ?? scenario.target ?? "target";
  const featureColumns = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const featureSchema = useMemo(() => scenario.feature_schema ?? {}, [scenario.feature_schema]);
  const fixedFeatures = useMemo(() => featureColumns.filter((f) => !extras.controllable.includes(f) && f !== extras.wear_feature), [featureColumns, extras]);
  const wearSpec: FeatureSpec | undefined = extras.wear_feature ? featureSchema[extras.wear_feature] : undefined;
  const label = useCallback((f: string) => featureLabel(scenario, f), [scenario]);

  const [settings, setSettings] = useState<OptimizeSettings>({ spec: extras.spec_default, objective: "profit", maxRejectRate: 0.05 });
  // The single source of truth for "what the line is running right now": the recipe
  // knobs plus the fixed context (material, wear). Auto-pilot and Apply both write here.
  const [context, setContext] = useState<Record_>(() => initialRecord(featureColumns, featureSchema));
  const [current, setCurrent] = useState<Scored | null>(null);
  const [scoring, setScoring] = useState(false);
  const [recommendation, setRecommendation] = useState<OptimizeResult | null>(null);
  const [progress, setProgress] = useState<OptimizeProgress | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [lines, setLines] = useState<Lines | null>(null);
  const [running, setRunning] = useState(false);
  const [autopilot, setAutopilot] = useState(true);
  const [autopilotStatus, setAutopilotStatus] = useState<string | null>(null);
  const [simMinutes, setSimMinutes] = useState(0);

  const contextRef = useRef(context);
  contextRef.current = context;
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const linesRef = useRef(lines);
  linesRef.current = lines;
  const runningRef = useRef(running);
  runningRef.current = running;
  const autopilotRef = useRef(autopilot);
  autopilotRef.current = autopilot;
  const busyRef = useRef(false);
  const cooldownRef = useRef(0);
  const simRef = useRef(0);
  const rngRef = useRef(mulberry32(7));

  const predictFn = useCallback(
    async (records: Record_[]) => (await predict(config.predictionUrl, scenario.slug, records, accessToken)).predictions,
    [scenario.slug, accessToken],
  );

  // Live re-score of the current recipe as the user drags a slider or changes the
  // spec — one record, a cheap call, debounced. Paused while the line runs (each
  // tick scores the current recipe at the line's own wear instead).
  useEffect(() => {
    if (running) return;
    const handle = setTimeout(async () => {
      setScoring(true);
      try {
        const [result] = await predictFn([context]);
        setCurrent(scoreRecord(context, result, extras, settings, 0));
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setScoring(false);
      }
    }, SCORE_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [context, settings, running, extras, predictFn]);

  const runOptimizer = useCallback(
    async (ctx: Record_, s: OptimizeSettings, quiet = false): Promise<OptimizeResult | null> => {
      try {
        const result = await optimize(ctx, extras, featureSchema, s, predictFn, quiet ? undefined : setProgress);
        setError(null);
        return result;
      } catch (e) {
        setError((e as Error).message);
        return null;
      } finally {
        if (!quiet) setProgress(null);
      }
    },
    [extras, featureSchema, predictFn],
  );

  async function optimizeNow() {
    setProgress({ stage: "Starting…", evaluated: 0, total: 1 });
    const result = await runOptimizer(context, settings);
    if (result) setRecommendation(result);
  }

  function updateFeature(feature: string, value: number | string) {
    setContext((c) => ({ ...c, [feature]: value }));
    // A hand-edited recipe invalidates a recommendation computed for the old one.
    if (extras.controllable.includes(feature) || fixedFeatures.includes(feature)) setRecommendation(null);
  }

  function applyRecipe(recipe: Record<string, number>, why: string) {
    const before = pickRecipe(contextRef.current, extras.controllable);
    const delta = formatRecipeDelta(before, recipe, label);
    setContext((c) => ({ ...c, ...recipe }));
    setRecommendation(null);
    if (runningRef.current && linesRef.current && delta !== "no change") {
      setLines((prev) =>
        prev
          ? {
              ...prev,
              optimized: {
                ...prev.optimized,
                recipeChanges: prev.optimized.recipeChanges + 1,
                events: [...prev.optimized.events, { t: simRef.current, kind: "recipe", text: `${why}: ${delta}` }],
              },
            }
          : prev,
      );
    }
  }

  function changeToolNow() {
    if (!extras.wear_feature || wearSpec?.type !== "numeric") return;
    if (runningRef.current && linesRef.current) {
      setLines((prev) => (prev ? { ...prev, optimized: changeTool(prev.optimized, extras, featureSchema, simRef.current, "Operator changed the tool") } : prev));
    }
    setContext((c) => ({ ...c, [extras.wear_feature as string]: wearSpec.min }));
    setRecommendation(null);
  }

  function startLine() {
    if (!lines) {
      const wear = extras.wear_feature ? Number(context[extras.wear_feature]) : null;
      const optimized = makeLine("optimized", "Optimized line", pickRecipe(context, extras.controllable), wear);
      // The "old way": the scenario's default recipe, never adjusted, tool changed
      // only when it's spent — same starting wear so the comparison is fair.
      const defaults = initialRecord(featureColumns, featureSchema);
      const baseline = makeLine("baseline", "Static baseline", pickRecipe(defaults, extras.controllable), wear);
      optimized.events.push({ t: 0, kind: "start", text: "Line started" });
      simRef.current = 0;
      setSimMinutes(0);
      setLines({ optimized, baseline });
    }
    setRunning(true);
  }

  function resetLine() {
    setRunning(false);
    setLines(null);
    setAutopilotStatus(null);
    simRef.current = 0;
    setSimMinutes(0);
    cooldownRef.current = 0;
  }

  const tick = useCallback(async () => {
    if (busyRef.current || !linesRef.current) return;
    busyRef.current = true;
    const s = settingsRef.current;
    const ctx = contextRef.current;
    const minutes = extras.sim_minutes_per_tick;
    const t = simRef.current + minutes;
    try {
      const prev = linesRef.current;
      const optimizedLine = { ...prev.optimized, recipe: pickRecipe(ctx, extras.controllable) };
      const records = [lineRecord(optimizedLine, ctx, extras), lineRecord(prev.baseline, ctx, extras)];
      const results = await predictFn(records);
      const scoredOpt = scoreRecord(records[0], results[0], extras, s, 0);
      const scoredBase = scoreRecord(records[1], results[1], extras, s, 0);
      let optimized = stepLine(optimizedLine, scoredOpt, extras, featureSchema, t, minutes, rngRef.current);
      const baseline = stepLine(prev.baseline, scoredBase, extras, featureSchema, t, minutes, rngRef.current);

      // Auto-pilot: only acts when the model itself says the running recipe is now
      // outside the tolerated reject rate — then weighs re-optimizing at the current
      // wear against changing the tool first and re-optimizing fresh, over a short
      // horizon, and applies whichever is worth more. Both options are real
      // optimizer runs against the real model, not scripted outcomes.
      const wearMin = wearSpec?.type === "numeric" ? wearSpec.min : null;
      if (autopilotRef.current && optimized.freshEdge) {
        // A fresh edge can usually take a more aggressive recipe than the one that
        // was tuned for a worn tool — re-optimize on it, apply only a real gain.
        setAutopilotStatus("Auto-pilot: fresh tool — re-optimizing the recipe for the new edge…");
        const fresh = await runOptimizer(lineRecord(optimized, ctx, extras), s, true);
        const delta = fresh ? formatRecipeDelta(optimized.recipe, fresh.best.recipe, label) : "no change";
        if (fresh && !fresh.noneFeasible && delta !== "no change" && fresh.best.econ.profitPerHour > scoredOpt.econ.profitPerHour * (1 + FRESH_EDGE_MIN_GAIN)) {
          optimized = {
            ...optimized,
            recipe: fresh.best.recipe,
            recipeChanges: optimized.recipeChanges + 1,
            events: [...optimized.events, { t, kind: "recipe", text: `Auto-pilot (fresh tool): ${delta} (${money(currency, scoredOpt.econ.profitPerHour)} → ${money(currency, fresh.best.econ.profitPerHour)}/h)` }],
          };
          setContext((c) => ({ ...c, ...fresh.best.recipe }));
          setRecommendation(null);
          setAutopilotStatus(`Auto-pilot: fresh tool — recipe re-tuned: ${delta}.`);
        } else {
          setAutopilotStatus("Auto-pilot: fresh tool — current recipe is still the best, keeping it.");
        }
        cooldownRef.current = AUTOPILOT_COOLDOWN_TICKS;
      } else if (autopilotRef.current && cooldownRef.current <= 0 && optimized.downtimeLeft === 0 && scoredOpt.econ.rejectProbability > s.maxRejectRate) {
        setAutopilotStatus(`Auto-pilot: ${targetLabel} drifting out of spec (${pct(scoredOpt.econ.rejectProbability)} reject risk) — re-optimizing…`);
        const ctxNow = lineRecord(optimized, ctx, extras);
        const optionA = await runOptimizer(ctxNow, s, true);
        let chosen = optionA;
        let swapTool = false;
        if (optionA && extras.wear_feature && wearMin !== null && econ.tool_life) {
          const optionB = await runOptimizer({ ...ctxNow, [extras.wear_feature]: wearMin }, s, true);
          if (optionB) {
            const h = AUTOPILOT_HORIZON_MINUTES;
            const valueA = optionA.noneFeasible ? -Infinity : (optionA.best.econ.profitPerHour * h) / 60;
            const valueB = (optionB.noneFeasible ? -Infinity : (optionB.best.econ.profitPerHour * (h - econ.tool_life.change_minutes)) / 60) - econ.tool_life.cost_per_edge;
            if (valueB > valueA) {
              chosen = optionB;
              swapTool = true;
            }
          }
        }
        if (chosen) {
          if (swapTool) optimized = changeTool(optimized, extras, featureSchema, t, "Auto-pilot: changed the tool — worth more than slowing down");
          const delta = formatRecipeDelta(optimized.recipe, chosen.best.recipe, label);
          if (!chosen.noneFeasible && delta !== "no change") {
            optimized = {
              ...optimized,
              recipe: chosen.best.recipe,
              recipeChanges: optimized.recipeChanges + 1,
              events: [
                ...optimized.events,
                {
                  t,
                  kind: "recipe",
                  text: `Auto-pilot: ${delta} (predicted ${targetLabel} ${scoredOpt.prediction.toFixed(2)} → ${chosen.best.prediction.toFixed(2)} ${units})`,
                },
              ],
            };
            setContext((c) => ({ ...c, ...chosen.best.recipe }));
            setRecommendation(null);
          }
          setAutopilotStatus(
            swapTool
              ? "Auto-pilot: tool changed and recipe re-optimized on the fresh edge."
              : chosen.noneFeasible
                ? "Auto-pilot: no recipe meets the spec at this wear — holding; change the tool or loosen the spec."
                : `Auto-pilot: recipe adjusted — ${delta}.`,
          );
        }
        cooldownRef.current = AUTOPILOT_COOLDOWN_TICKS;
      } else {
        cooldownRef.current -= 1;
      }

      simRef.current = t;
      setSimMinutes(t);
      setLines({ optimized, baseline });
      setCurrent(scoredOpt);
      if (extras.wear_feature && optimized.wear !== null) {
        const wear = Number(optimized.wear.toFixed(1));
        setContext((c) => ({ ...c, [extras.wear_feature as string]: wear }));
      }
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      busyRef.current = false;
    }
  }, [extras, featureSchema, predictFn, runOptimizer, wearSpec, econ.tool_life, label, targetLabel, units, currency]);

  useEffect(() => {
    if (!running) return;
    void tick();
    const id = setInterval(() => void tick(), extras.tick_seconds * 1000);
    return () => clearInterval(id);
  }, [running, extras.tick_seconds, tick]);

  const best = recommendation?.best ?? null;
  const improvement = current && best ? best.econ.profitPerHour - current.econ.profitPerHour : null;
  const savings = lines ? lines.optimized.profit - lines.baseline.profit : null;
  const rejectRate = (l: LineState) => (l.units > 0 ? l.rejects / l.units : 0);

  useCopilotReadable({
    description: `The ${scenario.title} recipe optimizer currently on screen: the recipe being run, its predicted ${targetLabel} and economics, the optimizer's recommendation (if any), and the live-line simulation's running totals versus a static baseline. Use this if asked why the recommended recipe is better, what the current reject risk is, or how much the optimizer has saved.`,
    value: {
      spec_limit: settings.spec,
      objective: settings.objective,
      tolerated_reject_rate: settings.maxRejectRate,
      current: current ? { recipe: current.recipe, predicted: current.prediction, ...current.econ } : null,
      recommendation: best ? { recipe: best.recipe, predicted: best.prediction, ...best.econ, none_feasible: recommendation?.noneFeasible } : null,
      live_line: lines
        ? {
            simulated_minutes: simMinutes,
            optimized: { units: lines.optimized.units, rejects: lines.optimized.rejects, profit: lines.optimized.profit, tool_changes: lines.optimized.toolChanges },
            baseline: { units: lines.baseline.units, rejects: lines.baseline.rejects, profit: lines.baseline.profit, tool_changes: lines.baseline.toolChanges },
            savings_vs_baseline: savings,
          }
        : null,
    },
  });

  const bestContributions = best ? mapContributions(best.record, best.contributions).map((i) => ({ ...i, label: label(i.label) })) : [];
  const specOption = extras.spec_options.find((o) => o.value === settings.spec);
  const wearPct = wearSpec?.type === "numeric" && extras.wear_feature ? (Number(context[extras.wear_feature]) - wearSpec.min) / (wearSpec.max - wearSpec.min || 1) : 0;

  const tradeoffXMax = Math.max(settings.spec * TRADEOFF_X_SPAN_FACTOR, (best?.prediction ?? 0) * 1.5, (current?.prediction ?? 0) * 1.2);
  const tradeoffHidden = recommendation ? recommendation.candidates.filter((c) => c.prediction > tradeoffXMax).length : 0;
  const tradeoffPoints = recommendation
    ? recommendation.candidates.filter((c) => c.prediction <= tradeoffXMax).map((c) => ({
        x: c.prediction,
        y: c.econ.unitsPerHour,
        color: c.round === 0 ? CHART_COLORS.text : c.feasible ? CHART_COLORS.green : CHART_COLORS.red,
        opacity: c.feasible ? 0.8 : 0.3,
        r: c.round === 0 ? 7 : c === best ? 7 : 2.8,
        ring: c.round === 0 || c === best,
        label: c.round === 0 ? "current" : c === best ? "recommended" : undefined,
        title: `${Object.entries(c.recipe)
          .map(([f, v]) => `${label(f)} ${v}`)
          .join(" · ")}\n${targetLabel} ${c.prediction.toFixed(2)} ${units} · ${c.econ.unitsPerHour.toFixed(0)}/h · ${money(currency, c.econ.profitPerHour)}/h · reject ${pct(c.econ.rejectProbability)}`,
      }))
    : [];
  const recommendedPoint = tradeoffPoints.find((p) => p.label === "recommended");
  if (recommendedPoint) recommendedPoint.color = CHART_COLORS.accent;

  const eventMarkers = lines
    ? lines.optimized.events
        .filter((e) => e.kind !== "start")
        .map((e) => {
          const point = lines.optimized.history.find((h) => h.t >= e.t) ?? lines.optimized.history[lines.optimized.history.length - 1];
          return { x: e.t, y: point?.prediction ?? 0, color: e.kind === "tool" ? CHART_COLORS.amber : CHART_COLORS.accent, glyph: e.kind === "tool" ? "⟲" : "⚙", title: e.text };
        })
    : [];
  const eventLog = lines ? [...lines.optimized.events].reverse().slice(0, EVENT_LOG_LENGTH) : [];

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <div className="opt-toolbar">
          <div>
            <h3>
              <Icon name="sparkle" /> Recipe optimizer
            </h3>
            <p className="panel-hint">
              The "take action" stage after prediction: every candidate recipe below is scored by this scenario's real, unmodified{" "}
              <code>/predict/{scenario.slug}</code> (~200 recipes per run, two rounds), then costed with a <strong>fictional</strong> per-unit economics model
              declared in the scenario's YAML — {currency}
              {econ.unit_value} per in-spec unit, {currency}
              {econ.reject_cost} rework per reject, {currency}
              {econ.machine_rate_per_hour}/h machine rate
              {econ.tool_life && `, ${currency}${econ.tool_life.cost_per_edge} + ${econ.tool_life.change_minutes} min per tool change`}. The live line further down is
              a client-side simulation — nothing here reads real telemetry or writes to a real controller.
            </p>
          </div>
          <div className="opt-controls">
            <label>
              Quality spec
              <select value={settings.spec} onChange={(e) => setSettings((s) => ({ ...s, spec: Number(e.target.value) }))}>
                {extras.spec_options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Objective <InfoButton text={OBJECTIVES.map((o) => `${o.label}: ${o.hint}`).join(" · ")} />
              <select value={settings.objective} onChange={(e) => setSettings((s) => ({ ...s, objective: e.target.value as Objective }))}>
                {OBJECTIVES.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Tolerated reject rate{" "}
              <InfoButton text="A recipe counts as in-spec only if the model's own probability of missing the spec (from its 90% prediction interval) is at or below this — not just its point prediction." />
              <select value={settings.maxRejectRate} onChange={(e) => setSettings((s) => ({ ...s, maxRejectRate: Number(e.target.value) }))}>
                {REJECT_TOLERANCES.map((r) => (
                  <option key={r} value={r}>
                    ≤ {pct(r, 0)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        {error && <p className="error">{error}</p>}
      </div>

      <div className="kpi-row opt-kpis">
        <StatTile
          label={`Predicted ${targetLabel}`}
          value={current ? `${current.prediction.toFixed(2)} ${units}` : "…"}
          sub={current ? (best ? `spec ≤ ${settings.spec} · rec. ${best.prediction.toFixed(2)}` : `spec ≤ ${settings.spec} · ±${(Z_90_DISPLAY * current.sigma).toFixed(2)} (90%)`) : scoring ? "scoring…" : undefined}
          color={current ? riskColor(current.econ.rejectProbability, settings.maxRejectRate) : undefined}
          highlight
        />
        <StatTile
          label="Reject risk"
          value={current ? pct(current.econ.rejectProbability) : "…"}
          sub={current ? (best ? `rec. ${pct(best.econ.rejectProbability)} · tolerated ≤ ${pct(settings.maxRejectRate, 0)}` : `tolerated ≤ ${pct(settings.maxRejectRate, 0)}`) : undefined}
          color={current ? riskColor(current.econ.rejectProbability, settings.maxRejectRate) : undefined}
          info="The model's own probability that a unit made with this recipe misses the spec, from its prediction interval — the expected reject cost is this × the rework cost."
        />
        <StatTile
          label="Cycle time"
          value={current ? `${current.econ.cycleMinutes.toFixed(2)} min` : "…"}
          sub={current ? `${current.econ.rate.toLocaleString(undefined, { maximumFractionDigits: 0 })} ${econ.cycle_time.rate_units}` : undefined}
          info={`${econ.cycle_time.fixed_minutes} min fixed per unit plus ${econ.cycle_time.work_per_unit.toLocaleString()} ÷ ${econ.cycle_time.rate_label.toLowerCase()} (${econ.cycle_time.rate_units}).`}
        />
        <StatTile
          label="Throughput"
          value={current ? `${current.econ.unitsPerHour.toFixed(0)} /h` : "…"}
          sub={current && best ? `rec. ${best.econ.unitsPerHour.toFixed(0)} /h` : undefined}
        />
        <StatTile
          label="Margin / unit"
          value={current ? money(currency, current.econ.margin, 2) : "…"}
          sub={current ? `mach. ${money(currency, current.econ.machineCost, 2)} · tool ${money(currency, current.econ.toolCost, 2)} · rej. ${money(currency, current.econ.expectedRejectCost, 2)}` : undefined}
          info={`${currency}${econ.unit_value} per in-spec unit minus machine time, amortized tooling, and reject risk × ${currency}${econ.reject_cost} rework.`}
        />
        <StatTile
          label="Profit rate"
          value={current ? `${money(currency, current.econ.profitPerHour)} /h` : "…"}
          sub={current ? `batch of ${econ.batch_size}: ${money(currency, current.econ.batchProfit)} · ${current.econ.batchHours.toFixed(1)} h` : undefined}
          color={improvement !== null && improvement > 1 ? CHART_COLORS.amber : CHART_COLORS.green}
        />
      </div>

      <div className="grid-2">
        <div className="panel-card">
          <h3>Current recipe</h3>
          <p className="panel-hint">The setpoints the line is running — drag any of them and the prediction and economics above re-score live.</p>
          <div className="feature-grid">
            {extras.controllable.map((f) => (
              <FeatureInput key={f} feature={f} spec={featureSchema[f]} value={context[f]} onChange={(v) => updateFeature(f, v)} />
            ))}
          </div>
          <h4>Fixed context</h4>
          <div className="feature-grid">
            {fixedFeatures.map((f) => (
              <FeatureInput key={f} feature={f} spec={featureSchema[f]} value={context[f]} onChange={(v) => updateFeature(f, v)} />
            ))}
            {extras.wear_feature && wearSpec?.type === "numeric" && (
              <div className="opt-wear">
                <div className="feature-input-label">
                  <span>
                    {label(extras.wear_feature)} {wearSpec.info && <InfoButton text={wearSpec.info} />}
                  </span>
                  <strong>
                    {Number(context[extras.wear_feature]).toFixed(1)} / {wearSpec.max}
                  </strong>
                </div>
                <div className="opt-wear-track">
                  <div className="opt-wear-fill" style={{ width: `${Math.min(100, wearPct * 100)}%`, background: wearPct > 0.75 ? CHART_COLORS.red : wearPct > 0.45 ? CHART_COLORS.amber : CHART_COLORS.green }} />
                </div>
                {!running && (
                  <input type="range" min={wearSpec.min} max={wearSpec.max} step={wearSpec.step ?? 1} value={Number(context[extras.wear_feature])} onChange={(e) => updateFeature(extras.wear_feature as string, Number(e.target.value))} />
                )}
                <button className="btn-secondary" onClick={changeToolNow}>
                  ⟲ Change tool{econ.tool_life && ` (${money(currency, econ.tool_life.cost_per_edge)}, ${econ.tool_life.change_minutes} min)`}
                </button>
              </div>
            )}
          </div>
          <div className="opt-actions">
            <button className="btn-primary" onClick={optimizeNow} disabled={progress !== null}>
              <Icon name="sparkle" size={14} /> {progress ? "Optimizing…" : "Optimize recipe"}
            </button>
            {best && (
              <button className="btn-primary" onClick={() => applyRecipe(best.recipe, "Operator applied the recommendation")}>
                ✓ Apply recommended recipe
              </button>
            )}
          </div>
          {progress && (
            <div className="opt-progress">
              <div className="opt-progress-track">
                <div className="opt-progress-fill" style={{ width: `${(progress.evaluated / progress.total) * 100}%` }} />
              </div>
              <span className="panel-hint">
                {progress.stage} ({progress.evaluated}/{progress.total} model calls)
              </span>
            </div>
          )}
        </div>

        <div className={`panel-card opt-reco${best ? " opt-reco--ready" : ""}`}>
          <h3>Recommendation</h3>
          {!best && (
            <p className="panel-hint">
              Click <strong>Optimize recipe</strong> to search every combination of the controllable setpoints for the one that {OBJECTIVES.find((o) => o.id === settings.objective)?.hint} — while keeping{" "}
              {targetLabel.toLowerCase()} within <strong>{specOption?.label ?? settings.spec}</strong>.
            </p>
          )}
          {best && current && recommendation && (
            <>
              {recommendation.noneFeasible && (
                <p className="opt-warning">
                  No recipe meets this spec at the current material and tool wear within the tolerated reject rate — showing the closest. Try a looser spec, a higher tolerated
                  reject rate, or change the tool.
                </p>
              )}
              <div className="opt-hero">
                <div className="opt-hero-item">
                  <span className="kpi-label">{targetLabel}</span>
                  <span className="opt-hero-value">
                    <span style={{ color: riskColor(current.econ.rejectProbability, settings.maxRejectRate) }}>{current.prediction.toFixed(2)}</span>
                    <span className="opt-hero-arrow">→</span>
                    <span style={{ color: riskColor(best.econ.rejectProbability, settings.maxRejectRate) }}>{best.prediction.toFixed(2)}</span>
                    <span className="opt-hero-units">{units}</span>
                  </span>
                </div>
                <div className="opt-hero-item">
                  <span className="kpi-label">Profit rate</span>
                  <span className="opt-hero-value">
                    {money(currency, current.econ.profitPerHour)}
                    <span className="opt-hero-arrow">→</span>
                    <span style={{ color: (improvement ?? 0) >= 0 ? CHART_COLORS.green : CHART_COLORS.red }}>{money(currency, best.econ.profitPerHour)}</span>
                    <span className="opt-hero-units">/h</span>
                  </span>
                  {improvement !== null && current.econ.profitPerHour !== 0 && (
                    <span className={`badge ${improvement >= 0 ? "badge--success" : "badge--danger"}`}>
                      {improvement >= 0 ? "+" : ""}
                      {money(currency, improvement)}/h · {improvement >= 0 ? "+" : ""}
                      {((improvement / Math.abs(current.econ.profitPerHour)) * 100).toFixed(0)}%
                    </span>
                  )}
                </div>
              </div>
              <table className="opt-table">
                <thead>
                  <tr>
                    <th>Setpoint</th>
                    <th>Now</th>
                    <th>Recommended</th>
                    <th>Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {extras.controllable.map((f) => {
                    const from = current.recipe[f];
                    const to = best.recipe[f];
                    const changed = from !== to;
                    return (
                      <tr key={f} className={changed ? "opt-table-changed" : ""}>
                        <td>{label(f)}</td>
                        <td>{from}</td>
                        <td>
                          <strong>{to}</strong>
                        </td>
                        <td style={{ color: changed ? (to > from ? CHART_COLORS.amber : CHART_COLORS.blue) : CHART_COLORS.dim }}>
                          {changed ? `${to > from ? "▲" : "▼"} ${Math.abs(to - from).toFixed(stepDecimals(featureSchema[f]))}` : "—"}
                        </td>
                      </tr>
                    );
                  })}
                  <tr className="opt-table-metrics">
                    <td>Reject risk</td>
                    <td>{pct(current.econ.rejectProbability)}</td>
                    <td>{pct(best.econ.rejectProbability)}</td>
                    <td />
                  </tr>
                  <tr className="opt-table-metrics">
                    <td>Throughput</td>
                    <td>{current.econ.unitsPerHour.toFixed(0)} /h</td>
                    <td>{best.econ.unitsPerHour.toFixed(0)} /h</td>
                    <td />
                  </tr>
                  <tr className="opt-table-metrics">
                    <td>Batch of {econ.batch_size}</td>
                    <td>
                      {money(currency, current.econ.batchProfit)} · {current.econ.batchHours.toFixed(1)} h
                    </td>
                    <td>
                      {money(currency, best.econ.batchProfit)} · {best.econ.batchHours.toFixed(1)} h
                    </td>
                    <td />
                  </tr>
                </tbody>
              </table>
              <p className="panel-hint">
                {recommendation.candidates.length} recipes scored in {(recommendation.elapsedMs / 1000).toFixed(1)} s · {recommendation.candidates.filter((c) => c.feasible).length} met the spec.
              </p>
              <h4>Why this recipe (SHAP on the recommended prediction)</h4>
              <BarList items={bestContributions} valueFormatter={(v) => v.toFixed(3)} />
            </>
          )}
        </div>
      </div>

      {recommendation && (
        <div className="panel-card">
          <h3>Quality vs throughput — every recipe the optimizer scored</h3>
          <p className="panel-hint">
            Each dot is one candidate recipe scored by the real model: green met the spec at the tolerated reject rate, red didn't. The recommendation is simply the best
            in-spec point for the chosen objective — hover a dot for its setpoints.
            {tradeoffHidden > 0 && ` ${tradeoffHidden} candidates beyond ${tradeoffXMax.toFixed(1)} ${units} are off the chart.`}
          </p>
          <TradeoffScatter
            points={tradeoffPoints}
            xRef={{ value: settings.spec, label: `spec ≤ ${settings.spec} ${units}`, color: CHART_COLORS.amber }}
            xLabel={`Predicted ${targetLabel} (${units})`}
            yLabel="Throughput (units / h)"
            width={760}
            height={300}
          />
        </div>
      )}

      <div className="panel-card">
        <div className="opt-toolbar">
          <div>
            <h3>
              <Icon name="factory" /> Live line
            </h3>
            <p className="panel-hint">
              Simulated production, {extras.sim_minutes_per_tick} line-minutes every {extras.tick_seconds}s: each tick scores both lines' recipes with the real model at their
              current tool wear, produces units at the recipe's cycle time, rolls each unit's in-spec outcome against the model's own reject probability, and ages the tool by
              real cutting time
              {econ.tool_life && ` (Taylor tool life, n = ${econ.tool_life.taylor_n})`}. The <strong>static baseline</strong> runs the scenario's default recipe and only
              changes a spent tool — the "old way". Auto-pilot intervenes only when the model says the running recipe has drifted past the tolerated reject rate.
            </p>
          </div>
          <div className="opt-controls opt-controls--line">
            <span className="panel-hint plant-floor-clock">🕒 t + {simClock(simMinutes)}</span>
            <label className="opt-toggle">
              <input type="checkbox" checked={autopilot} onChange={(e) => setAutopilot(e.target.checked)} /> Auto-pilot
            </label>
            {!running ? (
              <button className="btn-primary" onClick={startLine}>
                ▶ {lines ? "Resume line" : "Run line"}
              </button>
            ) : (
              <button className="btn-secondary" onClick={() => setRunning(false)}>
                ⏸ Pause line
              </button>
            )}
            {lines && (
              <button className="btn-secondary" onClick={resetLine}>
                ↺ Reset
              </button>
            )}
          </div>
        </div>
        {autopilotStatus && <p className="opt-autopilot-status">{autopilotStatus}</p>}

        {lines && (
          <>
            <div className="opt-lines">
              {[lines.optimized, lines.baseline].map((l) => (
                <div key={l.id} className={`opt-line-card opt-line-card--${l.id}`}>
                  <div className="opt-line-title">
                    {l.id === "optimized" ? "⚡ " : "▫ "}
                    {l.label}
                    {l.id === "optimized" && autopilot && <span className="badge badge--success">auto-pilot</span>}
                  </div>
                  <div className="opt-line-profit" style={{ color: l.profit >= 0 ? CHART_COLORS.green : CHART_COLORS.red }}>
                    {money(currency, l.profit)}
                  </div>
                  <div className="opt-line-stats">
                    <div>
                      <span>Units</span>
                      <strong>{l.units}</strong>
                    </div>
                    <div>
                      <span>Rejects</span>
                      <strong style={{ color: rejectRate(l) > settingsRef.current.maxRejectRate ? CHART_COLORS.red : CHART_COLORS.text }}>
                        {l.rejects} <small>({pct(rejectRate(l), 0)})</small>
                      </strong>
                    </div>
                    <div>
                      <span>Tool changes</span>
                      <strong>{l.toolChanges}</strong>
                    </div>
                    <div>
                      <span>Recipe changes</span>
                      <strong>{l.recipeChanges}</strong>
                    </div>
                    <div>
                      <span>{targetLabel}</span>
                      <strong style={{ color: l.last ? riskColor(l.last.econ.rejectProbability, settingsRef.current.maxRejectRate) : undefined }}>
                        {l.last ? `${l.last.prediction.toFixed(2)} ${units}` : "…"}
                      </strong>
                    </div>
                    {extras.wear_feature && l.wear !== null && (
                      <div>
                        <span>{label(extras.wear_feature)}</span>
                        <strong>{l.wear.toFixed(1)}</strong>
                      </div>
                    )}
                    {l.downtimeLeft > 0 && (
                      <div>
                        <span>Status</span>
                        <strong style={{ color: CHART_COLORS.amber }}>tool change…</strong>
                      </div>
                    )}
                  </div>
                </div>
              ))}
              <div className="opt-line-card opt-line-card--savings">
                <div className="opt-line-title">Optimizer vs static recipe</div>
                <div className="opt-line-profit" style={{ color: (savings ?? 0) >= 0 ? CHART_COLORS.accent : CHART_COLORS.red }}>
                  {(savings ?? 0) >= 0 ? "+" : ""}
                  {money(currency, savings ?? 0)}
                </div>
                <div className="opt-line-stats">
                  <div>
                    <span>Rejects avoided</span>
                    <strong>{Math.max(0, lines.baseline.rejects - lines.optimized.rejects)}</strong>
                  </div>
                  <div>
                    <span>Units Δ</span>
                    <strong>
                      {lines.optimized.units - lines.baseline.units >= 0 ? "+" : ""}
                      {lines.optimized.units - lines.baseline.units}
                    </strong>
                  </div>
                  <div>
                    <span>Per line-hour</span>
                    <strong>{simMinutes > 0 ? `${money(currency, ((savings ?? 0) * 60) / simMinutes)}/h` : "…"}</strong>
                  </div>
                </div>
              </div>
            </div>

            {lines.optimized.history.length > 1 && (
              <div className="grid-2 opt-charts">
                <div>
                  <h4>
                    Predicted {targetLabel} over line time {"·"} ⚙ recipe applied {"·"} ⟲ tool changed
                  </h4>
                  <MultiLineChart
                    series={[
                      { label: "Optimized", color: CHART_COLORS.accent, points: lines.optimized.history.map((h) => ({ x: h.t, y: h.prediction, lower: h.lower, upper: h.upper })) },
                      { label: "Static baseline", color: CHART_COLORS.dim, dashed: true, points: lines.baseline.history.map((h) => ({ x: h.t, y: h.prediction })) },
                    ]}
                    refLines={[{ y: settings.spec, label: `spec ≤ ${settings.spec}`, color: CHART_COLORS.amber }]}
                    markers={eventMarkers}
                    xLabel="Simulated line time (min)"
                    yLabel={`${targetLabel} (${units})`}
                    yMin={0}
                    yFormatter={(v) => v.toFixed(1)}
                  />
                </div>
                <div>
                  <h4>Cumulative profit</h4>
                  <MultiLineChart
                    series={[
                      { label: "Optimized", color: CHART_COLORS.green, points: lines.optimized.history.map((h) => ({ x: h.t, y: h.profit })) },
                      { label: "Static baseline", color: CHART_COLORS.dim, dashed: true, points: lines.baseline.history.map((h) => ({ x: h.t, y: h.profit })) },
                    ]}
                    xLabel="Simulated line time (min)"
                    yLabel={`Profit (${currency})`}
                    yFormatter={(v) => v.toFixed(0)}
                  />
                </div>
              </div>
            )}

            {eventLog.length > 0 && (
              <>
                <h4>Line log</h4>
                <ul className="opt-log">
                  {eventLog.map((e, i) => (
                    <li key={`${e.t}-${i}`} className={`opt-log--${e.kind}`}>
                      <span className="opt-log-time">t + {simClock(e.t)}</span>
                      {e.text}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
        {!lines && (
          <p className="panel-hint">
            Press <strong>Run line</strong> to start producing with the current recipe — then watch the tool wear, the model's predicted {targetLabel.toLowerCase()} drift, and
            the auto-pilot (or you, with <strong>Apply</strong>/<strong>Change tool</strong>) react — against a static baseline doing things the old way.
          </p>
        )}
      </div>
    </div>
  );
}
