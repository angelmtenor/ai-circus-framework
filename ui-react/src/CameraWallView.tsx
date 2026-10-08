import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { dlPredict, type CameraWallExtra, type DlModelInfo, type DlPrediction, type ScenarioSummary } from "./apiClient";
import { StatTile } from "./charts";
import { config } from "./config";
import { analyseCamera, groupCameras, offsetLabel, summarise, type CameraAnalysis, type WallCamera } from "./cameraWallLogic";
import { HeatmapImage, HeatmapLegend, labelsOf, pct, useDlImage, useDlSamples } from "./dlShared";
import "./cameraWall.css";

const SPEEDS = [1, 4, 16];
// Live scoring is a real /predict call per camera per tick: past 4x the wall would be
// firing >50 requests a second, so it falls back to the precomputed scores (and says so).
const LIVE_MAX_SPEED = 4;
const MAX_IN_FLIGHT = 8;

type Live = { p: number; ms: number };

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));

/**
 * Generic `camera_wall` renderer for a frame-sequence `deep_learning` scenario: one tile per
 * held-out camera, all advancing on one clock. Every tick each tile's current frame is
 * scored by the deployed model (`POST /predict`, no explanation) and the tile says whether
 * that score is `live` or a `replay` of the precomputed one (speed over 4x, the service
 * down, or the answer not back yet). An alert is the alarm switching on after
 * `consecutive_frames_to_alert` frames at/over the threshold; it is timed against the
 * recording's ground-truth event (frame offset 0), so a detection delay or a false alarm is
 * measured, not asserted. Opening a tile pauses the wall and asks for the occlusion heatmap.
 */
export function CameraWallView({
  scenario,
  extra,
  model,
  accessToken,
}: {
  scenario: ScenarioSummary;
  extra: CameraWallExtra;
  model: DlModelInfo;
  accessToken: string | null;
}) {
  const samples = useDlSamples(scenario.slug, accessToken);
  const positiveIndex = Math.max(
    0,
    labelsOf(scenario).findIndex((l) => l.key === extra.positive_label),
  );
  const pool = useMemo(
    () => groupCameras(samples.data ?? [], positiveIndex, extra.control_margin_seconds),
    [samples.data, positiveIndex, extra.control_margin_seconds],
  );
  // Which of the held-out cameras are on the wall: the scenario's default until the viewer swaps them.
  const [picked, setPicked] = useState<string[] | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [draft, setDraft] = useState<string[]>([]);
  const chosen = picked ?? (extra.default_cameras.length ? extra.default_cameras : pool.map((c) => c.name).slice(0, extra.wall_size));
  const cameras = useMemo(
    () => chosen.map((name) => pool.find((c) => c.name === name)).filter((c): c is WallCamera => c !== undefined),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pool, picked, extra.default_cameras, extra.wall_size],
  );
  const length = Math.max(0, ...cameras.map((c) => c.frames.length));
  const [step, setStep] = useState(0);
  const [running, setRunning] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [threshold, setThreshold] = useState(extra.alert_threshold);
  const [consecutive, setConsecutive] = useState(extra.consecutive_frames_to_alert);
  const [showCurve, setShowCurve] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  // Scores the model returned in this session, by sample id. A late answer is still correct
  // for *its* frame (keyed by id, never by arrival order), so there is nothing to discard.
  const live = useRef(new Map<string, Live>());
  const inFlight = useRef(new Set<string>());
  // Failed frames stay on the precomputed score instead of being retried in a loop.
  const failed = useRef(new Set<string>());
  // Bumped when a request settles, so frames skipped by the in-flight cap are picked up again.
  const [settled, rerender] = useReducer((n: number) => n + 1, 0);
  const atEnd = length > 0 && step >= length - 1;

  useEffect(() => {
    if (!running || selected || length === 0 || atEnd) return;
    const timer = setInterval(() => setStep((s) => Math.min(s + 1, length - 1)), extra.tick_ms / speed);
    return () => clearInterval(timer);
  }, [running, selected, length, atEnd, speed, extra.tick_ms]);

  useEffect(() => {
    if (!running || selected || speed > LIVE_MAX_SPEED) return;
    for (const camera of cameras) {
      const frame = camera.frames[Math.min(step, camera.frames.length - 1)];
      if (live.current.has(frame.id) || failed.current.has(frame.id) || inFlight.current.has(frame.id)) continue;
      if (inFlight.current.size >= MAX_IN_FLIGHT) break;
      inFlight.current.add(frame.id);
      dlPredict(config.dlInferenceUrl, scenario.slug, { sample_id: frame.id, explain: false, similar: 0 }, accessToken)
        .then((r) => {
          const p = r.probabilities.find((x) => x.key === extra.positive_label)?.probability;
          if (p !== undefined) live.current.set(frame.id, { p, ms: r.latency_ms });
          setLiveError(null);
        })
        .catch((e: unknown) => {
          failed.current.add(frame.id);
          setLiveError(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          inFlight.current.delete(frame.id);
          rerender();
        });
    }
  }, [step, settled, running, selected, speed, cameras, scenario.slug, accessToken, extra.positive_label]);

  const scored = cameras.map((camera) => {
    const last = Math.min(step, camera.frames.length - 1);
    const ps = camera.frames.slice(0, last + 1).map((f) => live.current.get(f.id)?.p ?? f.pPre);
    return { camera, last, ps, analysis: analyseCamera(camera, ps, threshold, consecutive, step, extra.early_grace_seconds) };
  });
  const summary = summarise(cameras, scored.map((s) => s.analysis), step);
  const feed = scored
    .flatMap((s) => s.analysis.alerts)
    .sort((a, b) => b.index - a.index || a.camera.localeCompare(b.camera));
  const noun = extra.event_noun;
  const withEvent = cameras.filter((c) => !c.control).length;
  const isControl = (name: string) => cameras.find((c) => c.name === name)?.control === true;

  if (samples.error) return <p className="error">{samples.error}</p>;
  if (!samples.data) return <div className="app-loading">Loading the camera recordings…</div>;
  if (cameras.length === 0) {
    return (
      <div className="tab-panel">
        <p className="error">
          This model was published without camera / frame metadata — retrain it (<code>make dl-train SCENARIOS={scenario.slug}</code>).
        </p>
      </div>
    );
  }

  const selectedCamera = cameras.find((c) => c.name === selected) ?? null;
  const selectedFrame = selectedCamera && selectedCamera.frames[Math.min(step, selectedCamera.frames.length - 1)];

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <div className="dl-board-head">
          <div>
            <h3>{extra.title}</h3>
            <p className="panel-hint" style={{ marginTop: "-0.2rem" }}>
              {cameras.length} held-out cameras, one frame a minute, scored by {model.base_model.split("/")[1]} as they arrive.
              {extra.note ? ` ${extra.note}` : ""}
            </p>
          </div>
          <div className="dl-toolbar">
            <button className="btn-secondary" onClick={() => setRunning((r) => !r)}>
              {running && !atEnd ? "⏸ Pause" : "▶ Play"}
            </button>
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} title="Playback speed">
              {SPEEDS.map((s) => (
                <option key={s} value={s}>
                  {s}×
                </option>
              ))}
            </select>
            <button className="btn-secondary" onClick={() => setStep(length - 1)}>
              ⏭ End
            </button>
            <button
              className="btn-secondary"
              onClick={() => {
                setDraft(chosen);
                setPickerOpen((o) => !o);
              }}
            >
              🎛 Change cameras
            </button>
            <button
              className="btn-secondary"
              onClick={() => {
                setStep(0);
                setRunning(true);
                setSelected(null);
              }}
            >
              ↺ Restart
            </button>
          </div>
        </div>
        <div className="kpi-row">
          <StatTile label="Cameras online" value={`${cameras.length} / ${cameras.length}`} sub={`frame ${Math.min(step + 1, length)} of ${length}`} />
          <StatTile
            label={`${capitalize(noun)} detected`}
            value={withEvent ? `${summary.detected} / ${withEvent}` : "—"}
            sub={
              withEvent
                ? summary.undetected
                  ? `${summary.undetected} not yet flagged`
                  : "none waiting"
                : "no fire on this wall"
            }
            color={summary.undetected ? "var(--amber)" : undefined}
            highlight
          />
          <StatTile
            label="Mean time to alert"
            value={summary.meanDelayMinutes === null ? "—" : `${summary.meanDelayMinutes.toFixed(1)} min`}
            sub="after it first became visible"
            info="Minutes between the first frame in which the recording's ground truth marks the plume as visible and the alarm switching on, averaged over the cameras that raised one."
          />
          <StatTile
            label="False alarms"
            value={String(summary.falseAlarms)}
            sub={`of ${summary.alerts} alert${summary.alerts === 1 ? "" : "s"}${summary.early ? ` · ${summary.early} early` : ""}`}
            color={summary.falseAlarms ? "var(--red)" : "var(--green)"}
            info={`An alert raised more than ${Math.round(extra.early_grace_seconds / 60)} min before the ${noun} appeared in that recording (or at all, on a no-fire control). An alert within ${Math.round(extra.early_grace_seconds / 60)} min before it is counted as an early detection instead: the annotation is when a person first saw the plume.`}
          />
        </div>
        <div className="dl-threshold">
          <label>
            Alert when P({noun}) ≥ <strong>{pct(threshold, 0)}</strong>
            <input type="range" min={0.1} max={0.95} step={0.05} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
          </label>
          <label>
            for <strong>{consecutive}</strong> frame{consecutive === 1 ? "" : "s"} in a row
            <input type="range" min={1} max={6} step={1} value={consecutive} onChange={(e) => setConsecutive(Number(e.target.value))} />
          </label>
          <label className="dl-check">
            <input type="checkbox" checked={showCurve} onChange={(e) => setShowCurve(e.target.checked)} /> Show each camera's whole recording
          </label>
        </div>
        {liveError && (
          <p className="panel-hint wall-warn">Live scoring unavailable ({liveError}) — tiles marked replay use the model's precomputed scores.</p>
        )}
      </div>

      {pickerOpen && (
        <div className="panel-card wall-picker">
          <h4>
            Choose the cameras on the wall <span className="wall-when">{draft.length} / {extra.wall_size}</span>
          </h4>
          <p className="panel-hint">
            All of these lookouts were held out of training. A <strong>no fire</strong> camera shows a lookout before any fire: every alert on it is a false alarm.
          </p>
          <div className="wall-picker-grid">
            {pool.map((c) => {
              const on = draft.includes(c.name);
              return (
                <label key={c.name} className={`wall-pick${on ? " wall-pick--on" : ""}`}>
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={!on && draft.length >= extra.wall_size}
                    onChange={() => setDraft(on ? draft.filter((n) => n !== c.name) : [...draft, c.name])}
                  />
                  <strong>{c.name}</strong>
                  <span className={c.control ? "wall-pick-tag wall-pick-tag--clear" : "wall-pick-tag"}>
                    {c.control ? "no fire" : "fire"}
                  </span>
                  <span className="wall-when">{c.frames.length} frames</span>
                </label>
              );
            })}
          </div>
          <div className="dl-toolbar">
            <button
              className="btn-secondary"
              disabled={draft.length < 2 || sameSet(draft, chosen)}
              onClick={() => {
                setPicked(pool.map((c) => c.name).filter((n) => draft.includes(n)));
                setStep(0);
                setRunning(true);
                setSelected(null);
                setPickerOpen(false);
              }}
            >
              ✓ Apply and restart
            </button>
            <button className="btn-secondary" onClick={() => setPickerOpen(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="wall-main">
        <div className="wall-grid">
          {scored.map(({ camera, last, ps, analysis }) => (
            <CameraTile
              key={camera.name}
              slug={scenario.slug}
              accessToken={accessToken}
              camera={camera}
              index={last}
              p={ps[last]}
              live={live.current.get(camera.frames[last].id)}
              analysis={analysis}
              threshold={threshold}
              history={ps}
              showCurve={showCurve}
              noun={noun}
              ended={step > camera.frames.length - 1}
              onOpen={() => setSelected(camera.name)}
            />
          ))}
        </div>
        <aside className="panel-card wall-feed">
          <h4>Alert feed</h4>
          {feed.length === 0 && <p className="panel-hint">No alerts yet — every camera is quiet.</p>}
          <ul>
            {feed.map((a) => (
              <li key={`${a.camera}-${a.index}`} className={`wall-alert${a.falseAlarm ? " wall-alert--false" : a.early ? " wall-alert--early" : ""}`}>
                <strong>{a.camera}</strong> <span className="wall-when">{offsetLabel(a.seq)}</span>
                <div>
                  {a.early
                    ? `Early — ${noun} flagged ${Math.round(-a.seq / 60)} min before it was annotated as visible`
                    : a.falseAlarm
                    ? isControl(a.camera)
                      ? `False alarm — ${noun} flagged, but this lookout has no fire`
                      : `False alarm — ${noun} flagged before it had appeared`
                    : a.seq === 0
                      ? `${capitalize(noun)} flagged in the first frame it was visible`
                      : `${capitalize(noun)} flagged ${Math.round(a.seq / 60)} min after it first appeared`}
                  <span className="wall-p"> · P {pct(a.p, 0)}</span>
                </div>
              </li>
            ))}
          </ul>
        </aside>
      </div>

      {selectedCamera && selectedFrame && (
        <WallDetail
          slug={scenario.slug}
          extra={extra}
          camera={selectedCamera}
          frame={selectedFrame}
          accessToken={accessToken}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

/** One line under a tile: what this camera has done so far. */
function tileVerdict(camera: WallCamera, analysis: CameraAnalysis, index: number, noun: string): { text: string; tone: "ok" | "warn" | "bad" | "idle" } {
  const falseAlarms = analysis.alerts.filter((a) => a.falseAlarm).length;
  const early = analysis.alerts.find((a) => a.early);
  if (camera.control) {
    if (falseAlarms) return { text: `✗ ${falseAlarms} false alarm${falseAlarms === 1 ? "" : "s"} — there is no fire here`, tone: "bad" };
    return { text: index > 0 ? "✓ no alerts — correctly quiet" : "watching a lookout with no fire", tone: index > 0 ? "ok" : "idle" };
  }
  const parts: string[] = [];
  if (analysis.detectedAt !== null) {
    const m = Math.round(camera.frames[analysis.detectedAt].seq / 60);
    parts.push(m < 0 ? `✓ ${noun} flagged ${-m} min early` : m === 0 ? `✓ ${noun} flagged at T+0` : `✓ ${noun} flagged T+${m} min`);
  } else if (index >= camera.ignition) {
    parts.push(`… ${noun} not flagged yet`);
  }
  if (falseAlarms) parts.push(`${falseAlarms} false alarm${falseAlarms === 1 ? "" : "s"}`);
  else if (early && analysis.detectedAt === null) parts.push("early alert");
  return { text: parts.join(" · ") || "watching", tone: falseAlarms ? "warn" : analysis.detectedAt !== null ? "ok" : "idle" };
}

function CameraTile({
  slug,
  accessToken,
  camera,
  index,
  p,
  live,
  analysis,
  threshold,
  history,
  showCurve,
  noun,
  ended,
  onOpen,
}: {
  slug: string;
  accessToken: string | null;
  camera: WallCamera;
  index: number;
  p: number;
  live: Live | undefined;
  analysis: CameraAnalysis;
  threshold: number;
  history: number[];
  showCurve: boolean;
  noun: string;
  ended: boolean;
  onOpen: () => void;
}) {
  const frame = camera.frames[index];
  // The next two frames are fetched ahead so the feed never blanks between ticks.
  const current = useDlImage(slug, frame.id, accessToken);
  useDlImage(slug, camera.frames[Math.min(index + 1, camera.frames.length - 1)].id, accessToken);
  useDlImage(slug, camera.frames[Math.min(index + 2, camera.frames.length - 1)].id, accessToken);
  const shown = useRef<string | null>(null);
  if (current) shown.current = current;

  const alarm = analysis.alarmOn[index] === true;
  const missed = index >= camera.ignition && analysis.detectedAt === null;
  const state = alarm ? "alarm" : missed ? "missed" : "clear";
  const last = camera.frames.length - 1;
  const verdict = tileVerdict(camera, analysis, index, noun);
  const x = (i: number) => (last ? (i / last) * 100 : 0);
  const y = (v: number) => 30 - Math.min(1, Math.max(0, v)) * 28;
  const path = (values: number[]) => values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");

  return (
    <button className={`wall-tile wall-tile--${state}`} onClick={onOpen} title="Open: pause and show where the model sees smoke">
      <div className="wall-frame">
        {shown.current ? <img src={shown.current} alt={`${camera.name} at ${offsetLabel(frame.seq)}`} /> : <span className="dl-image-placeholder" />}
        <span className="wall-cam">{camera.name}</span>
        <span className="wall-time">{offsetLabel(frame.seq)}</span>
        {camera.control && <span className="wall-chip wall-chip--clear">no fire · control</span>}
        {ended && <span className="wall-chip wall-chip--end">recording ended</span>}
        <span className={`wall-mode wall-mode--${live ? "live" : "replay"}`}>{live ? `live · ${Math.round(live.ms)} ms` : "replay"}</span>
        {alarm && <span className="wall-banner">⚠ {noun} detected</span>}
      </div>
      <div className="wall-meta">
        <span className="wall-prob" style={{ color: p >= threshold ? "var(--red)" : "var(--green)" }}>
          P {pct(p, 0)}
        </span>
        <svg viewBox="0 0 100 32" preserveAspectRatio="none" className="wall-spark" aria-hidden>
          {showCurve && <path d={path(camera.frames.map((f) => f.pPre))} className="wall-spark-full" />}
          <line x1="0" x2="100" y1={y(threshold)} y2={y(threshold)} className="wall-spark-threshold" />
          {camera.ignition <= last && <line x1={x(camera.ignition)} x2={x(camera.ignition)} y1="0" y2="32" className="wall-spark-event" />}
          <path d={path(history)} className="wall-spark-line" />
          <circle cx={x(index)} cy={y(p)} r="1.8" className="wall-spark-dot" />
        </svg>
      </div>
      <div className={`wall-verdict wall-verdict--${verdict.tone}`}>{verdict.text}</div>
    </button>
  );
}

function WallDetail({
  slug,
  extra,
  camera,
  frame,
  accessToken,
  onClose,
}: {
  slug: string;
  extra: CameraWallExtra;
  camera: WallCamera;
  frame: { id: string; seq: number };
  accessToken: string | null;
  onClose: () => void;
}) {
  const src = useDlImage(slug, frame.id, accessToken);
  const [result, setResult] = useState<DlPrediction | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setResult(null);
    setError(null);
    dlPredict(config.dlInferenceUrl, slug, { sample_id: frame.id, explain: true, target: extra.positive_label, similar: 0 }, accessToken)
      .then((r) => !cancelled && setResult(r))
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [slug, frame.id, accessToken, extra.positive_label]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const grid = result?.explanation?.type === "heatmap" ? result.explanation.grid : null;
  const p = result?.probabilities.find((x) => x.key === extra.positive_label)?.probability;
  return (
    <div className="wall-detail" role="dialog" aria-label={`${camera.name} detail`} onClick={onClose}>
      <div className="panel-card wall-detail-card" onClick={(e) => e.stopPropagation()}>
        <div className="dl-board-head">
          <h3>
            {camera.name} · {offsetLabel(frame.seq)}
          </h3>
          <button className="btn-secondary" onClick={onClose}>
            ✕ Close — resume wall
          </button>
        </div>
        <div className="wall-detail-frame">
          <HeatmapImage src={src} grid={grid} opacity={0.6} size={520} />
        </div>
        {error && <p className="error">{error}</p>}
        {!result && !error && <p className="panel-hint">Scoring this frame and tracing where the model finds {extra.event_noun}…</p>}
        {result && (
          <p className="panel-hint">
            P({extra.event_noun}) <strong>{p === undefined ? "—" : pct(p, 1)}</strong> · {Math.round(result.latency_ms)} ms · highlighted: the regions whose removal lowers
            that probability most.
          </p>
        )}
        <HeatmapLegend />
      </div>
    </div>
  );
}
