import { useEffect, useMemo, useState } from "react";
import { dlPredict, type DlModelInfo, type DlPrediction, type ReadingRoomExtra, type ScenarioSummary } from "./apiClient";
import { BarList, CHART_COLORS, StatTile } from "./charts";
import { config } from "./config";
import { HeatmapImage, HeatmapLegend, labelName, labelsOf, pct, SimilarCases, Thumbnail, useDlImage, useDlSamples, useSeededOrder, probText } from "./dlShared";

type Study = { id: string; trueLabel: string; score: number; arrival: number; accession: string };
type Order = "ai" | "fifo";
const SHIFT_START_MINUTES = 8 * 60;

function clock(minutes: number): string {
  const m = Math.round(SHIFT_START_MINUTES + minutes);
  return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/**
 * One reader working a worklist where a study arrives every `arrival` minutes and each
 * read takes `perRead` minutes — whenever the reader frees up, the next study is picked
 * by `order`. Returns each study's report time (minutes after its arrival).
 */
function simulateTurnaround(studies: Study[], order: Order, threshold: number, perRead: number): Map<string, number> {
  const pending = [...studies].sort((a, b) => a.arrival - b.arrival);
  const done = new Map<string, number>();
  let now = 0;
  const queue: Study[] = [];
  while (pending.length || queue.length) {
    while (pending.length && pending[0].arrival <= now) queue.push(pending.shift()!);
    if (!queue.length) {
      now = pending[0].arrival;
      continue;
    }
    queue.sort((a, b) =>
      order === "fifo"
        ? a.arrival - b.arrival
        : Number(b.score >= threshold) - Number(a.score >= threshold) || b.score - a.score || a.arrival - b.arrival,
    );
    const next = queue.shift()!;
    now += perRead;
    done.set(next.id, now - next.arrival);
  }
  return done;
}

/**
 * Generic `reading_room` renderer: the published held-out studies become a simulated
 * shift's worklist, ordered either first-in-first-out or by the model's probability of
 * `positive_label` (flagged above `priority_threshold`). The viewer offers the controls
 * a reader expects (window/level, zoom, invert) plus the occlusion heatmap; the reader
 * confirms or overrides, then sees the expert label. The KPI simulation replays the
 * *same* worklist both ways to show what AI triage does to time-to-report for the
 * positive (urgent) studies — the actual value of the model in a radiology workflow.
 */
export function ReadingRoomView({
  scenario,
  extra,
  model,
  accessToken,
}: {
  scenario: ScenarioSummary;
  extra: ReadingRoomExtra;
  model: DlModelInfo;
  accessToken: string | null;
}) {
  const samples = useDlSamples(scenario.slug, accessToken);
  const ordered = useSeededOrder(samples.data, 11);
  const positiveIndex = Math.max(0, labelsOf(scenario).findIndex((l) => l.key === extra.positive_label));
  const positiveName = labelName(scenario, extra.positive_label);
  const negativeKey = labelsOf(scenario).find((l) => l.key !== extra.positive_label)?.key ?? "0";
  const [threshold, setThreshold] = useState(extra.priority_threshold);
  const [order, setOrder] = useState<Order>("ai");
  const [reads, setReads] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<string | null>(null);

  const studies: Study[] = useMemo(
    () =>
      ordered.slice(0, extra.worklist_size).map((s, i) => ({
        id: s.id,
        trueLabel: s.label,
        score: s.probs[positiveIndex] ?? 0,
        arrival: i * extra.arrival_minutes,
        accession: `ACC-${(24091 + i * 7).toString().padStart(6, "0")}`,
      })),
    [ordered, extra.worklist_size, extra.arrival_minutes, positiveIndex],
  );

  const worklist = useMemo(() => {
    const list = [...studies];
    if (order === "fifo") return list.sort((a, b) => a.arrival - b.arrival);
    return list.sort(
      (a, b) => Number(b.score >= threshold) - Number(a.score >= threshold) || b.score - a.score || a.arrival - b.arrival,
    );
  }, [studies, order, threshold]);

  useEffect(() => {
    if (!selected && worklist.length) setSelected(worklist[0].id);
  }, [worklist, selected]);

  const sim = useMemo(() => {
    const fifo = simulateTurnaround(studies, "fifo", threshold, extra.minutes_per_read);
    const ai = simulateTurnaround(studies, "ai", threshold, extra.minutes_per_read);
    const positives = studies.filter((s) => s.trueLabel === extra.positive_label);
    const mean = (m: Map<string, number>, list: Study[]) => (list.length ? list.reduce((acc, s) => acc + (m.get(s.id) ?? 0), 0) / list.length : 0);
    const flagged = studies.filter((s) => s.score >= threshold);
    const tp = flagged.filter((s) => s.trueLabel === extra.positive_label).length;
    const negatives = studies.length - positives.length;
    const fp = flagged.length - tp;
    return {
      fifoPositive: mean(fifo, positives),
      aiPositive: mean(ai, positives),
      fifoAll: mean(fifo, studies),
      aiAll: mean(ai, studies),
      positives: positives.length,
      sensitivity: positives.length ? tp / positives.length : 1,
      specificity: negatives ? (negatives - fp) / negatives : 1,
      flagged: flagged.length,
    };
  }, [studies, threshold, extra.minutes_per_read, extra.positive_label]);

  const readCount = Object.keys(reads).length;
  const agreement = useMemo(() => {
    const ids = Object.keys(reads);
    const withAi = ids.filter((id) => {
      const s = studies.find((x) => x.id === id);
      return s && (reads[id] === extra.positive_label) === s.score >= threshold;
    }).length;
    const correct = ids.filter((id) => studies.find((x) => x.id === id)?.trueLabel === reads[id]).length;
    return { withAi, correct, total: ids.length };
  }, [reads, studies, threshold, extra.positive_label]);

  if (samples.error) return <p className="error">{samples.error}</p>;
  if (!samples.data) return <div className="app-loading">Loading the worklist…</div>;
  const study = studies.find((s) => s.id === selected) ?? null;
  const speedup = sim.fifoPositive > 0 ? 1 - sim.aiPositive / sim.fifoPositive : 0;

  const decide = (label: string) => {
    if (!study) return;
    setReads((r) => ({ ...r, [study.id]: label }));
    const next = worklist.find((s) => s.id !== study.id && !reads[s.id]);
    if (next) setTimeout(() => setSelected(next.id), 900);
  };

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>AI-prioritized reading room</h3>
        <p className="panel-hint" style={{ marginTop: "-0.2rem" }}>
          A simulated shift: {studies.length} chest X-rays arrive one every {extra.arrival_minutes} min, a read takes{" "}
          {extra.minutes_per_read} min — the reader can't keep up, so reading order decides how long a sick child waits.
        </p>
        <div className="kpi-row">
          <StatTile
            label={`${positiveName} time-to-report`}
            value={`${Math.round(sim.aiPositive)} min`}
            sub={`vs ${Math.round(sim.fifoPositive)} min first-come-first-served`}
            highlight
          />
          <StatTile
            label="Faster for urgent studies"
            value={pct(Math.max(0, speedup), 0)}
            color={speedup > 0 ? "var(--green)" : undefined}
            info={`Mean minutes from arrival to report for the ${sim.positives} studies with ${positiveName} (expert label), AI order vs FIFO. Other studies wait longer — the average over all studies is unchanged (${Math.round(sim.aiAll)} vs ${Math.round(sim.fifoAll)} min).`}
          />
          <StatTile label="AI sensitivity" value={pct(sim.sensitivity, 0)} sub={`specificity ${pct(sim.specificity, 0)} · ${sim.flagged} flagged`} />
          <StatTile
            label="Your reads"
            value={`${readCount}`}
            sub={readCount ? `${pct(agreement.correct / agreement.total, 0)} match the expert · ${pct(agreement.withAi / agreement.total, 0)} agree with AI` : "confirm or override a study"}
          />
        </div>
        <div className="dl-toolbar">
          <label>
            Priority threshold: <strong>{pct(threshold, 0)}</strong>
            <input type="range" min={0.05} max={0.95} step={0.05} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
          </label>
          <div className="dl-segmented">
            <button className={order === "ai" ? "active" : ""} onClick={() => setOrder("ai")}>
              AI-prioritized
            </button>
            <button className={order === "fifo" ? "active" : ""} onClick={() => setOrder("fifo")}>
              First in, first out
            </button>
          </div>
        </div>
      </div>

      <div className="dl-reading">
        <div className="panel-card dl-worklist">
          <h3>Worklist</h3>
          {worklist.map((s) => {
            const flagged = s.score >= threshold;
            const read = reads[s.id];
            return (
              <button key={s.id} className={`dl-worklist-row${selected === s.id ? " active" : ""}${read ? " dl-worklist-row--read" : ""}`} onClick={() => setSelected(s.id)}>
                <Thumbnail slug={scenario.slug} sampleId={s.id} accessToken={accessToken} size={40} />
                <span className="dl-worklist-meta">
                  <span>{s.accession}</span>
                  <span className="panel-hint">arrived {clock(s.arrival)}</span>
                </span>
                <span className={`dl-priority${flagged ? " dl-priority--flag" : ""}`}>{flagged ? `⚑ ${probText(s.score)}` : probText(s.score)}</span>
                {read && <span className={read === s.trueLabel ? "dl-ok" : "dl-bad"}>{read === s.trueLabel ? "✓" : "✗"}</span>}
              </button>
            );
          })}
        </div>

        {study && (
          <StudyViewer
            key={study.id}
            scenario={scenario}
            study={study}
            threshold={threshold}
            positiveKey={extra.positive_label}
            negativeKey={negativeKey}
            read={reads[study.id]}
            onDecide={decide}
            accessToken={accessToken}
            modelName={model.base_model.split("/")[1] ?? model.base_model}
          />
        )}
      </div>

      <div className="panel-card">
        <h3>Minutes from arrival to report — {positiveName} studies</h3>
        <BarList
          items={[
            { label: "First in, first out", value: sim.fifoPositive },
            { label: `AI-prioritized (≥ ${pct(threshold, 0)})`, value: sim.aiPositive },
          ]}
          signed={false}
          neutralColor={CHART_COLORS.accent}
          valueFormatter={(v) => `${Math.round(v)} min`}
        />
        <p className="panel-hint">
          Same studies, same reader, same {extra.minutes_per_read}-minute reads — only the order changes. Lower the threshold to flag more
          studies (higher sensitivity, more false alarms jumping the queue); raise it for fewer, surer flags.
        </p>
      </div>
    </div>
  );
}

function StudyViewer({
  scenario,
  study,
  threshold,
  positiveKey,
  negativeKey,
  read,
  onDecide,
  accessToken,
  modelName,
}: {
  scenario: ScenarioSummary;
  study: Study;
  threshold: number;
  positiveKey: string;
  negativeKey: string;
  read: string | undefined;
  onDecide: (label: string) => void;
  accessToken: string | null;
  modelName: string;
}) {
  const image = useDlImage(scenario.slug, study.id, accessToken);
  const [result, setResult] = useState<DlPrediction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showHeat, setShowHeat] = useState(true);
  const [opacity, setOpacity] = useState(0.5);
  const [brightness, setBrightness] = useState(1);
  const [contrast, setContrast] = useState(1.1);
  const [zoom, setZoom] = useState(1);
  const [invert, setInvert] = useState(false);

  useEffect(() => {
    dlPredict(config.dlInferenceUrl, scenario.slug, { sample_id: study.id, target: positiveKey, similar: 3 }, accessToken)
      .then(setResult)
      .catch((e) => setError((e as Error).message));
  }, [scenario.slug, study.id, positiveKey, accessToken]);

  const flagged = study.score >= threshold;
  const positiveName = labelName(scenario, positiveKey);
  return (
    <div className="panel-card dl-viewer">
      <div className="dl-board-head">
        <h3>
          {study.accession} <span className="panel-hint">· arrived {clock(study.arrival)}</span>
        </h3>
        <span className={`dl-priority${flagged ? " dl-priority--flag" : ""}`}>
          {flagged ? `⚑ AI flag: ${positiveName}` : "AI: no flag"} · {probText(study.score)}
        </span>
      </div>
      <div className="dl-viewer-body">
        <div className="dl-viewer-image">
          <HeatmapImage
            src={image}
            grid={showHeat && result?.explanation?.type === "heatmap" ? result.explanation.grid : null}
            opacity={opacity}
            brightness={brightness}
            contrast={contrast}
            zoom={zoom}
            invert={invert}
            size={380}
          />
          <div className="dl-viewer-controls">
            <label>
              Level <input type="range" min={0.5} max={1.8} step={0.05} value={brightness} onChange={(e) => setBrightness(Number(e.target.value))} />
            </label>
            <label>
              Window <input type="range" min={0.6} max={2.5} step={0.05} value={contrast} onChange={(e) => setContrast(Number(e.target.value))} />
            </label>
            <label>
              Zoom <input type="range" min={1} max={2.5} step={0.1} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} />
            </label>
            <label className="dl-check">
              <input type="checkbox" checked={invert} onChange={(e) => setInvert(e.target.checked)} /> Invert
            </label>
            <label className="dl-check">
              <input type="checkbox" checked={showHeat} onChange={(e) => setShowHeat(e.target.checked)} /> AI heatmap
            </label>
            {showHeat && (
              <label>
                Opacity <input type="range" min={0} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
              </label>
            )}
            <HeatmapLegend />
          </div>
        </div>
        <div className="dl-viewer-side">
          {error && <p className="error">{error}</p>}
          {!result && !error && <p className="panel-hint">Reading…</p>}
          {result && (
            <>
              <div className="kpi-label">AI second read ({modelName})</div>
              <div className="dl-verdict-label">{labelName(scenario, result.predicted)}</div>
              <p className="panel-hint">
                P({positiveName}) = {probText(study.score)} · heatmap shows evidence for {positiveName} · {result.latency_ms} ms
              </p>
              <div className="dl-decide">
                <button className="btn-primary dl-decide-positive" disabled={!!read} onClick={() => onDecide(positiveKey)}>
                  Report {positiveName}
                </button>
                <button className="btn-secondary" disabled={!!read} onClick={() => onDecide(negativeKey)}>
                  Report {labelName(scenario, negativeKey)}
                </button>
              </div>
              {read && (
                <p className={read === study.trueLabel ? "dl-ok" : "dl-bad"}>
                  You reported {labelName(scenario, read)} — expert label: <strong>{labelName(scenario, study.trueLabel)}</strong>{" "}
                  {read === study.trueLabel ? "✓" : "✗"}
                </p>
              )}
              <div className="kpi-label" style={{ marginTop: "0.8rem" }}>
                Similar prior studies
              </div>
              <SimilarCases scenario={scenario} cases={result.similar} accessToken={accessToken} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
