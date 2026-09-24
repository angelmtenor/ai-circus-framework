import { useEffect, useMemo, useState } from "react";
import { dlPredict, type DlModelInfo, type DlPrediction, type ScenarioSummary, type TriageBoardExtra } from "./apiClient";
import { CHART_COLORS, MultiLineChart, StatTile } from "./charts";
import { config } from "./config";
import { labelName, pct, ProbabilityBars, TokenHighlights, TopWords, topOf, useDlSamples, useSeededOrder, probText } from "./dlShared";

const REVIEW = "__review__";
const TONE_COLOR: Record<string, string> = { critical: "var(--red)", warning: "var(--amber)", info: "var(--blue)", ok: "var(--green)" };

type Card = { id: string; text: string; trueLabel: string; probs: number[]; arrivedAt: number };

/**
 * Generic `triage_board` renderer: the published held-out messages arrive one by one
 * (a replay, client-side, every `tick_seconds`) and are routed by the model's top
 * class into the scenario's care-pathway lanes — or to the human-review lane when its
 * confidence is under the threshold (selective prediction). Routing uses the deployed
 * model's own precomputed probabilities, so the board is exactly what that model would
 * do; opening a card re-scores it live for the word-level explanation. Ground truth is
 * known for every replayed message, so the KPIs are real: automation rate, routing
 * accuracy, and — the safety number — urgent cases sent anywhere but the urgent lane.
 */
export function TriageBoardView({
  scenario,
  extra,
  model,
  accessToken,
}: {
  scenario: ScenarioSummary;
  extra: TriageBoardExtra;
  model: DlModelInfo;
  accessToken: string | null;
}) {
  const samples = useDlSamples(scenario.slug, accessToken);
  const stream = useSeededOrder(samples.data, 7);
  const [threshold, setThreshold] = useState(extra.confidence_threshold);
  const [arrived, setArrived] = useState(6);
  const [running, setRunning] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [openId, setOpenId] = useState<string | null>(null);

  const laneOfLabel = useMemo(() => {
    const m = new Map<string, string>();
    extra.lanes.forEach((lane) => lane.labels.forEach((l) => m.set(l, lane.key)));
    return m;
  }, [extra.lanes]);
  const urgentLane = extra.lanes.find((l) => l.tone === "critical")?.key;

  useEffect(() => {
    if (!running || arrived >= stream.length) return;
    const id = setInterval(() => setArrived((n) => Math.min(n + 1, stream.length)), (extra.tick_seconds * 1000) / speed);
    return () => clearInterval(id);
  }, [running, speed, arrived, stream.length, extra.tick_seconds]);

  const cards: Card[] = useMemo(
    () => stream.slice(0, arrived).map((s, i) => ({ id: s.id, text: s.text ?? "", trueLabel: s.label, probs: s.probs, arrivedAt: i })),
    [stream, arrived],
  );

  const route = (card: Card) => {
    if (overrides[card.id]) return overrides[card.id];
    const top = topOf(scenario, card.probs);
    return top.confidence >= threshold ? (laneOfLabel.get(top.key) ?? REVIEW) : REVIEW;
  };

  const kpis = useMemo(() => {
    const routed = cards.map((c) => ({ card: c, lane: route(c), trueLane: laneOfLabel.get(c.trueLabel) }));
    const auto = routed.filter((r) => r.lane !== REVIEW && !overrides[r.card.id]);
    const correct = auto.filter((r) => r.lane === r.trueLane).length;
    const urgent = routed.filter((r) => r.trueLane === urgentLane);
    const urgentMissed = urgent.filter((r) => r.lane !== urgentLane && r.lane !== REVIEW).length;
    return {
      total: routed.length,
      auto: auto.length,
      accuracy: auto.length ? correct / auto.length : 1,
      review: routed.filter((r) => r.lane === REVIEW).length,
      urgent: urgent.length,
      urgentCaught: urgent.filter((r) => r.lane === urgentLane).length,
      urgentMissed,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cards, threshold, overrides, laneOfLabel, urgentLane]);

  // Lane-level selective-prediction curve over the whole held-out set.
  const curve = useMemo(() => {
    const all = samples.data ?? [];
    return Array.from({ length: 20 }, (_, i) => {
      const t = i / 20;
      const kept = all.filter((s) => topOf(scenario, s.probs).confidence >= t);
      const ok = kept.filter((s) => laneOfLabel.get(topOf(scenario, s.probs).key) === laneOfLabel.get(s.label)).length;
      return { t, coverage: all.length ? kept.length / all.length : 0, accuracy: kept.length ? ok / kept.length : 1 };
    });
  }, [samples.data, scenario, laneOfLabel]);

  if (samples.error) return <p className="error">{samples.error}</p>;
  if (!samples.data) return <div className="app-loading">Loading the message stream…</div>;

  const lanes = [
    ...extra.lanes.map((l) => ({ key: l.key, label: l.label, description: l.description, color: TONE_COLOR[l.tone] })),
    { key: REVIEW, label: extra.review_lane_label, description: `Model confidence under ${pct(threshold, 0)} — a clinician decides.`, color: "var(--purple)" },
  ];
  const open = cards.find((c) => c.id === openId) ?? null;

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <div className="dl-board-head">
          <div>
            <h3>Live triage board</h3>
            <p className="panel-hint" style={{ marginTop: "-0.2rem" }}>
              Incoming patient messages (held-out cases, replayed) routed by {model.base_model.split("/")[1]} to a care pathway. Illustrative
              routing only — not clinical guidance.
            </p>
          </div>
          <div className="dl-toolbar">
            <button className="btn-secondary" onClick={() => setRunning((r) => !r)}>
              {running ? "⏸ Pause" : "▶ Resume"}
            </button>
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} title="Replay speed">
              <option value={1}>1×</option>
              <option value={4}>4×</option>
              <option value={16}>16×</option>
            </select>
            <button className="btn-secondary" onClick={() => setArrived(stream.length)}>
              ⏭ All {stream.length}
            </button>
            <button className="btn-secondary" onClick={() => (setArrived(0), setOverrides({}), setOpenId(null))}>
              ↺ Reset
            </button>
          </div>
        </div>
        <div className="kpi-row">
          <StatTile label="Messages" value={`${kpis.total} / ${stream.length}`} />
          <StatTile label="Auto-routed" value={pct(kpis.total ? kpis.auto / kpis.total : 0, 0)} sub={`${kpis.review} to ${extra.review_lane_label.toLowerCase()}`} />
          <StatTile label="Routing accuracy" value={pct(kpis.accuracy, 0)} sub="auto-routed, right pathway" highlight />
          <StatTile
            label="Urgent caught"
            value={`${kpis.urgentCaught} / ${kpis.urgent}`}
            sub={kpis.urgentMissed ? `${kpis.urgentMissed} urgent case(s) mis-routed` : "none mis-routed"}
            color={kpis.urgentMissed ? "var(--red)" : "var(--green)"}
            info="Truly urgent messages routed to the urgent lane. The rest went to human review (safe) or — the dangerous case — to a non-urgent lane."
          />
        </div>
        <div className="dl-threshold">
          <label>
            Confidence threshold for automatic routing: <strong>{pct(threshold, 0)}</strong>
            <input type="range" min={0} max={0.95} step={0.05} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
          </label>
        </div>
      </div>

      <div className="dl-board">
        {lanes.map((lane) => {
          const inLane = cards.filter((c) => route(c) === lane.key).reverse();
          return (
            <div key={lane.key} className="dl-lane" style={{ borderTopColor: lane.color }}>
              <div className="dl-lane-head">
                <span>{lane.label}</span>
                <span className="dl-lane-count" style={{ background: lane.color }}>
                  {inLane.length}
                </span>
              </div>
              <p className="dl-lane-desc">{lane.description}</p>
              {inLane.map((c) => {
                const top = topOf(scenario, c.probs);
                const trueLane = laneOfLabel.get(c.trueLabel);
                const ok = lane.key === REVIEW ? null : lane.key === trueLane;
                return (
                  <button
                    key={c.id}
                    className={`dl-card${c.arrivedAt === arrived - 1 ? " dl-card--new" : ""}${openId === c.id ? " active" : ""}`}
                    onClick={() => setOpenId(c.id)}
                  >
                    <span className="dl-card-text">“{c.text.length > 110 ? `${c.text.slice(0, 110)}…` : c.text}”</span>
                    <span className="dl-card-meta">
                      <span className="scenario-meta-pill">
                        {labelName(scenario, top.key)} {probText(top.confidence)}
                      </span>
                      {overrides[c.id] && <span className="scenario-meta-pill">clinician override</span>}
                      {ok !== null && <span className={ok ? "dl-ok" : "dl-bad"}>{ok ? "✓" : "✗"}</span>}
                    </span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>

      {open && (
        <TriageDetail
          scenario={scenario}
          card={open}
          lanes={lanes}
          currentLane={route(open)}
          onOverride={(lane) => setOverrides((o) => ({ ...o, [open.id]: lane }))}
          onClose={() => setOpenId(null)}
          accessToken={accessToken}
        />
      )}

      <div className="panel-card">
        <h3>Automation vs. safety trade-off</h3>
        <div className="dl-chart">
        <MultiLineChart
          series={[
            { label: "routing accuracy (auto-routed)", color: CHART_COLORS.green, points: curve.map((p) => ({ x: p.t, y: p.accuracy })) },
            { label: "share auto-routed", color: CHART_COLORS.blue, points: curve.map((p) => ({ x: p.t, y: p.coverage })), dashed: true },
          ]}
          markers={[
            {
              x: Math.round(threshold * 20) / 20,
              y: curve[Math.min(19, Math.round(threshold * 20))]?.accuracy ?? 1,
              color: CHART_COLORS.amber,
              glyph: "◆",
              title: "current threshold",
            },
          ]}
          xLabel="confidence threshold"
          yLabel="share"
          yMin={0}
          yFormatter={(v) => pct(v, 0)}
        />
        </div>
        <p className="panel-hint">
          Over all {samples.data.length} held-out messages: every notch of threshold sends more messages to a nurse and makes the automatic
          routing more accurate — the operating point is a clinical-governance decision, not a model property.
        </p>
      </div>
    </div>
  );
}

function TriageDetail({
  scenario,
  card,
  lanes,
  currentLane,
  onOverride,
  onClose,
  accessToken,
}: {
  scenario: ScenarioSummary;
  card: Card;
  lanes: { key: string; label: string }[];
  currentLane: string;
  onOverride: (lane: string) => void;
  onClose: () => void;
  accessToken: string | null;
}) {
  const [result, setResult] = useState<DlPrediction | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setResult(null);
    dlPredict(config.dlInferenceUrl, scenario.slug, { sample_id: card.id, similar: 0 }, accessToken)
      .then(setResult)
      .catch((e) => setError((e as Error).message));
  }, [scenario.slug, card.id, accessToken]);
  return (
    <div className="panel-card dl-detail">
      <div className="dl-board-head">
        <h3>Message detail</h3>
        <button className="btn-secondary" onClick={onClose}>
          Close
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!result && !error && <p className="panel-hint">Explaining…</p>}
      {result?.explanation?.type === "tokens" && (
        <div className="dl-detail-grid">
          <div>
            <TokenHighlights text={card.text} tokens={result.explanation.tokens} />
            <TopWords tokens={result.explanation.tokens} />
            <p className="panel-hint">
              Why the model suggests <strong>{labelName(scenario, result.predicted)}</strong> — red words for, blue against. Dataset label:{" "}
              <strong>{labelName(scenario, card.trueLabel)}</strong>.
            </p>
          </div>
          <div>
            <ProbabilityBars items={result.probabilities} highlight={result.predicted} limit={4} />
            <label className="dl-override">
              Route to{" "}
              <select value={currentLane} onChange={(e) => onOverride(e.target.value)}>
                {lanes.map((l) => (
                  <option key={l.key} value={l.key}>
                    {l.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
      )}
    </div>
  );
}
