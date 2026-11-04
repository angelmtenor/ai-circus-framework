/**
 * Generic 5th workspace tab (`ui_extras: {kind: row_triage}`, see scenario_schema.RowTriageExtra):
 * rows arrive one by one and go to a review lane or an automatic one by a threshold the reader moves.
 * Scores and SHAP are the precomputed out-of-fold ones, so each row was scored by a model that never
 * saw it and a tick calls nothing. DOM only, no frame loop, ticks stop while the page is hidden, and
 * each lane draws at most LANE_CARDS cards so the page never grows.
 */
import { useEffect, useMemo, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { datasetSample, outOfFoldScores, type RowTriageExtra, type ScenarioSummary } from "./apiClient";
import { BarList, CHART_COLORS, MultiLineChart, StatTile } from "./charts";
import { config } from "./config";
import { explainByFeature, featureLabel } from "./predictUtils";
import "./rowTriage.css";

const LANE_CARDS = 12;

type Item = { id: string; probability: number; actual: 0 | 1; contributions: Record<string, number> };

const pct = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`;
const withArticle = (noun: string) => `${/^[aeiou]/i.test(noun) ? "an" : "a"} ${noun}`;

/** A fixed, shuffled-looking arrival order, so the same replay every time. */
function hashed(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619);
  return h >>> 0;
}

export function RowTriageView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as RowTriageExtra;
  const items = `${extras.item_noun}s`;
  const [stream, setStream] = useState<Item[] | null>(null);
  const [rocAuc, setRocAuc] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(extras.review_at);
  const [arrived, setArrived] = useState(24);
  const [running, setRunning] = useState(true);
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([outOfFoldScores(config.predictionUrl, scenario.slug, accessToken), datasetSample(config.predictionUrl, scenario.slug, 30000, accessToken)])
      .then(([scores, sample]) => {
        if (!live) return;
        const target = scenario.target ?? "";
        const column = sample.id_column ?? "";
        const actual = new Map(sample.rows.map((row) => [String(row[column]), Number(row[target]) === 1 ? 1 : 0] as const));
        const rows = scores.rows.flatMap((r): Item[] => {
          const known = actual.get(r.id);
          return known === undefined ? [] : [{ id: r.id, probability: r.probability, actual: known, contributions: r.contributions }];
        });
        setStream(rows.sort((a, b) => hashed(a.id) - hashed(b.id)));
        setRocAuc(scores.roc_auc);
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [scenario.slug, scenario.target, accessToken]);

  const total = stream?.length ?? 0;
  useEffect(() => {
    if (!running || total === 0) return;
    const id = window.setInterval(() => {
      if (!document.hidden) setArrived((n) => Math.min(n + 1, total));
    }, extras.tick_seconds * 1000);
    return () => window.clearInterval(id);
  }, [running, total, extras.tick_seconds]);

  const shown = useMemo(() => (stream ?? []).slice(0, arrived), [stream, arrived]);
  const kpis = useMemo(() => {
    const review = shown.filter((i) => i.probability >= threshold);
    const needing = shown.filter((i) => i.actual === 1);
    const missed = needing.filter((i) => i.probability < threshold).length;
    return { review: review.length, needing: needing.length, missed, needless: review.filter((i) => i.actual === 0).length };
  }, [shown, threshold]);

  // The whole held-out story, once: what each threshold costs.
  const curve = useMemo(() => {
    const all = stream ?? [];
    const needing = all.filter((i) => i.actual === 1);
    return Array.from({ length: 21 }, (_, step) => {
      const t = step / 20;
      return {
        t,
        workload: all.length ? all.filter((i) => i.probability >= t).length / all.length : 0,
        missed: needing.length ? needing.filter((i) => i.probability < t).length / needing.length : 0,
      };
    });
  }, [stream]);

  const open = stream?.find((i) => i.id === openId) ?? null;
  const drivers = useMemo(() => {
    if (!open) return [];
    return explainByFeature(scenario.feature_columns ?? [], open.probability, open.contributions)
      .items.slice(0, 8)
      .map((d) => ({ label: featureLabel(scenario, d.feature), value: d.value }));
  }, [open, scenario]);

  useCopilotReadable({
    description: `${scenario.title} triage on screen: ${items} routed by a threshold on the out-of-fold probability of needing a ${extras.reviewer_noun}.`,
    value: { threshold, seen: shown.length, sent_to_review: kpis.review, needing_review: kpis.needing, missed: kpis.missed },
  });

  if (error) return <div className="tab-panel"><div className="error">Could not load the triage: {error}</div></div>;
  if (!stream) return <div className="tab-panel"><div className="panel-hint">Loading the {extras.item_noun} stream…</div></div>;

  const newest = [...shown].reverse();
  const lanes = [
    { key: "auto", label: extras.auto_lane_label, color: CHART_COLORS.green, members: newest.filter((i) => i.probability < threshold), bad: (i: Item) => i.actual === 1 },
    { key: "review", label: extras.review_lane_label, color: CHART_COLORS.purple, members: newest.filter((i) => i.probability >= threshold), bad: (i: Item) => i.actual === 0 },
  ];
  const yes = scenario.target_value_labels?.["1"] ?? "Needs review";
  const no = scenario.target_value_labels?.["0"] ?? "Does not";

  return (
    <div className="tab-panel triage">
      <div className="triage-head">
        <div>
          <h2>{extras.title}</h2>
          {extras.subtitle && <p className="panel-hint">{extras.subtitle}</p>}
        </div>
        <div className="triage-actions">
          <button className="btn-secondary" onClick={() => setRunning((r) => !r)}>
            {running ? "⏸ Pause" : "▶ Resume"}
          </button>
          <button className="btn-secondary" onClick={() => setArrived(total)}>
            ⏭ All {total}
          </button>
        </div>
      </div>

      <div className="kpi-row triage-kpis">
        <StatTile label={`${items} seen`} value={`${shown.length} / ${total}`} sub={rocAuc !== null ? `ROC AUC ${rocAuc.toFixed(3)}, out of fold` : undefined} />
        <StatTile label={`${extras.reviewer_noun} workload`} value={pct(shown.length ? kpis.review / shown.length : 0)} sub={`${kpis.review} ${items}, ${kpis.needless} of them needlessly`} />
        <StatTile
          label="Missed"
          value={`${kpis.missed} of ${kpis.needing}`}
          sub={`needed ${withArticle(extras.reviewer_noun)}, passed automatically`}
          color={kpis.missed ? CHART_COLORS.red : CHART_COLORS.green}
          highlight
        />
      </div>

      <div className="panel-card triage-threshold">
        <label>
          Send to {extras.review_lane_label.toLowerCase()} from a probability of <strong>{pct(threshold)}</strong>
          <input type="range" min={0.05} max={0.95} step={0.05} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
        </label>
      </div>

      <div className="triage-lanes">
        {lanes.map((lane) => (
          <div key={lane.key} className="triage-lane" style={{ borderTopColor: lane.color }}>
            <div className="triage-lane-head">
              <span>{lane.label}</span>
              <b>{lane.members.length}</b>
            </div>
            {lane.members.slice(0, LANE_CARDS).map((item) => (
              <button key={item.id} className={`triage-card${item.id === openId ? " triage-card--on" : ""}`} onClick={() => setOpenId(item.id)} aria-pressed={item.id === openId}>
                <span className="triage-card-id">
                  {extras.item_noun} {item.id}
                </span>
                <span className="triage-card-score">{pct(item.probability)}</span>
                <span className={lane.bad(item) ? "triage-bad" : "triage-ok"} title={item.actual ? yes : no}>
                  {lane.bad(item) ? "✗" : "✓"}
                </span>
              </button>
            ))}
            {lane.members.length > LANE_CARDS && <p className="panel-hint">Newest {LANE_CARDS} shown.</p>}
          </div>
        ))}
      </div>

      <div className="grid-2 triage-detail">
        <div className="panel-card">
          <h3>What each threshold costs</h3>
          <MultiLineChart
            series={[
              { label: `${extras.reviewer_noun} workload`, color: CHART_COLORS.blue, points: curve.map((p) => ({ x: p.t, y: p.workload })), dashed: true },
              { label: "missed", color: CHART_COLORS.red, points: curve.map((p) => ({ x: p.t, y: p.missed })) },
            ]}
            xLabel="threshold"
            yLabel="share"
            yMin={0}
            yFormatter={(v) => pct(v)}
          />
          <p className="panel-hint">
            Over all {total} {items}. The operating point is a policy decision, not a property of the model.
          </p>
        </div>
        <div className="panel-card">
          {open ? (
            <>
              <h3>
                {extras.item_noun} {open.id}: {pct(open.probability)}
              </h3>
              <p className="panel-hint">Actually: {open.actual ? yes : no}.</p>
              <BarList items={drivers} valueFormatter={(v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)} pts`} />
              <p className="panel-hint">Red pushes towards {withArticle(extras.reviewer_noun)}, green away, from a model that never saw this {extras.item_noun}.</p>
            </>
          ) : (
            <>
              <h3>Why this {extras.item_noun}?</h3>
              <p className="panel-hint">Open a card to see what pushed its score.</p>
            </>
          )}
        </div>
      </div>

      <p className="panel-hint triage-note">
        Real {items} of this scenario's dataset, one every {extras.tick_seconds} s, each scored by a model that never saw it. A replay: nothing here reads a live line.
      </p>
    </div>
  );
}
