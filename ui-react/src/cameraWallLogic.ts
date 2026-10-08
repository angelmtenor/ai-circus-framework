import type { DlSample } from "./apiClient";

/** One published frame of a camera's recording. `seq` = seconds from the moment the event
 * first became visible (negative = before it); `pPre` = the deployed model's precomputed
 * P(event) for this frame (samples.json), used for the replay fallback and the full curve. */
export type WallFrame = { id: string; seq: number; pPre: number };
/** `control`: the recording holds no event at all (ignition = past the last frame) — a feed where every alert is a false alarm. */
export type WallCamera = { name: string; frames: WallFrame[]; ignition: number; control: boolean };

/** Group the published samples (recording order, tagged `group`/`seq`) into one camera each. */
export function groupCameras(samples: DlSample[], positiveIndex: number): WallCamera[] {
  const byCamera = new Map<string, WallFrame[]>();
  for (const s of samples) {
    if (s.group == null || s.seq == null) continue;
    const frames = byCamera.get(s.group) ?? [];
    frames.push({ id: s.id, seq: s.seq, pPre: s.probs[positiveIndex] ?? 0 });
    byCamera.set(s.group, frames);
  }
  return [...byCamera].map(([name, frames]) => {
    frames.sort((a, b) => a.seq - b.seq);
    const first = frames.findIndex((f) => f.seq >= 0);
    const ignition = first === -1 ? frames.length : first;
    return { name, frames, ignition, control: ignition === frames.length };
  });
}

export type WallAlert = { camera: string; index: number; seq: number; p: number; falseAlarm: boolean };
export type CameraAnalysis = {
  /** Alarm state of every frame up to `upto` (inclusive). */
  alarmOn: boolean[];
  /** Rising edges of the alarm: a new alert each time smoke persists after a clear spell. */
  alerts: WallAlert[];
  /** Index of the first frame at/after the event at which the alarm was on, or null. */
  detectedAt: number | null;
};

/**
 * Alerting rule: the alarm is on once `consecutive` frames in a row reach `threshold`, and
 * off as soon as a frame falls below it. An alert is the alarm switching on; it is a *false
 * alarm* when that happens before the event (negative `seq`). Detection = the first frame
 * at/after the event with the alarm on — so a camera already ringing at ignition counts as
 * detected at once, never as "missed". `p[i]` of null = not scored (the run is broken).
 */
export function analyseCamera(
  camera: WallCamera,
  p: (number | null)[],
  threshold: number,
  consecutive: number,
  upto: number,
): CameraAnalysis {
  const alarmOn: boolean[] = [];
  const alerts: WallAlert[] = [];
  let run = 0;
  let detectedAt: number | null = null;
  const last = Math.min(upto, camera.frames.length - 1);
  for (let i = 0; i <= last; i++) {
    const v = p[i];
    run = v != null && v >= threshold ? run + 1 : 0;
    const on = run >= consecutive;
    alarmOn.push(on);
    if (on && !alarmOn[i - 1]) {
      alerts.push({ camera: camera.name, index: i, seq: camera.frames[i].seq, p: v ?? 0, falseAlarm: camera.frames[i].seq < 0 });
    }
    if (on && detectedAt === null && camera.frames[i].seq >= 0) detectedAt = i;
  }
  return { alarmOn, alerts, detectedAt };
}

export type WallSummary = {
  alerts: number;
  falseAlarms: number;
  detected: number;
  /** Cameras whose event has already happened at `upto` without an alarm. */
  undetected: number;
  /** Mean minutes from the event first being visible to the alarm, over detected cameras. */
  meanDelayMinutes: number | null;
};

export function summarise(cameras: WallCamera[], analyses: CameraAnalysis[], upto: number): WallSummary {
  let detected = 0;
  let undetected = 0;
  let delay = 0;
  let falseAlarms = 0;
  let alerts = 0;
  cameras.forEach((camera, c) => {
    const a = analyses[c];
    alerts += a.alerts.length;
    falseAlarms += a.alerts.filter((x) => x.falseAlarm).length;
    if (a.detectedAt !== null) {
      detected += 1;
      delay += camera.frames[a.detectedAt].seq / 60;
    } else if (camera.ignition <= Math.min(upto, camera.frames.length - 1)) {
      undetected += 1;
    }
  });
  return { alerts, falseAlarms, detected, undetected, meanDelayMinutes: detected ? delay / detected : null };
}

/** "T−12 min" / "T+0" / "T+7 min": time relative to the event first being visible. */
export function offsetLabel(seq: number): string {
  const minutes = Math.round(seq / 60);
  if (minutes === 0) return "T+0";
  return `T${minutes < 0 ? "−" : "+"}${Math.abs(minutes)} min`;
}
