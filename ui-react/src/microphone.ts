// Which microphone voice mode listens to: the browser's own choice (the default), or one picked in Settings or in the
// mic button's "no sound" notice. A per-viewer convenience kept in this browser, as device ids are: it may be empty or
// unavailable (private window, blocked storage), and then the browser's choice applies.
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

export interface MicrophoneChoice {
  deviceId: string;
  /** Kept to find the device again when the browser renews its ids (cleared site data), and to name it unplugged. */
  label: string;
}

const KEY = "ai-open.microphone";

function stored(): MicrophoneChoice | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(KEY) ?? "null") as Partial<MicrophoneChoice> | null;
    return value && typeof value.deviceId === "string" && value.deviceId && typeof value.label === "string"
      ? { deviceId: value.deviceId, label: value.label }
      : null;
  } catch {
    return null;
  }
}

let choice: MicrophoneChoice | null = stored();
const listeners = new Set<() => void>();

/** Calls back when another microphone is chosen; returns the unsubscribe. */
export function subscribeMicrophone(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** `null` goes back to the browser's own choice. */
export function chooseMicrophone(next: MicrophoneChoice | null): void {
  if (next?.deviceId === choice?.deviceId) return;
  choice = next;
  try {
    if (next) window.localStorage.setItem(KEY, JSON.stringify(next));
    else window.localStorage.removeItem(KEY);
  } catch {
    // storage blocked: the choice lasts for this page only
  }
  for (const listener of [...listeners]) listener();
}

/** The chosen microphone; `null` is the browser's own choice. */
export function useMicrophoneChoice(): MicrophoneChoice | null {
  return useSyncExternalStore(subscribeMicrophone, () => choice);
}

/** The audio inputs the browser names; empty until the microphone has been allowed once (it hides them before). */
async function listMicrophones(): Promise<MicrophoneChoice[]> {
  const devices = (await navigator.mediaDevices?.enumerateDevices?.()) ?? [];
  return devices
    .filter((d) => d.kind === "audioinput" && d.deviceId && d.label)
    .map((d) => ({ deviceId: d.deviceId, label: d.label }));
}

/** The audio inputs, kept up to date as devices come and go; `refresh` after the microphone is allowed. */
export function useMicrophones(): { microphones: MicrophoneChoice[]; refresh: () => void } {
  const [microphones, setMicrophones] = useState<MicrophoneChoice[]>([]);
  const refresh = useCallback(() => {
    listMicrophones()
      .then(setMicrophones)
      .catch(() => {}); // best-effort list: the browser's choice still works
  }, []);
  useEffect(() => {
    refresh();
    const media = navigator.mediaDevices;
    media?.addEventListener?.("devicechange", refresh);
    return () => media?.removeEventListener?.("devicechange", refresh);
  }, [refresh]);
  return { microphones, refresh };
}

/** The device is gone or its id changed, as opposed to the browser refusing the microphone. */
function isMissingDevice(err: unknown): boolean {
  const name = (err as { name?: string } | null)?.name; // an OverconstrainedError is not a DOMException everywhere
  return name === "OverconstrainedError" || name === "NotFoundError";
}

/**
 * Opens the chosen microphone: by its id, or by its name when the browser has renewed its ids. An unplugged one falls
 * back to the browser's choice, so voice keeps working (the mic button names the microphone it used if it is silent).
 */
export async function openMicrophone(): Promise<MediaStream> {
  const wanted = choice;
  const open = (deviceId: string) => navigator.mediaDevices.getUserMedia({ audio: { deviceId: { exact: deviceId } } });
  if (wanted) {
    try {
      return await open(wanted.deviceId);
    } catch (err) {
      if (!isMissingDevice(err)) throw err;
    }
    const renewed = (await listMicrophones()).find((m) => m.label === wanted.label && m.deviceId !== wanted.deviceId);
    if (renewed) return open(renewed.deviceId);
  }
  return navigator.mediaDevices.getUserMedia({ audio: true });
}

/** Below this peak there is no speech, as agui-voice's SILENT_PEAK_DBFS: a muted or wrong microphone sends zeros. */
const SILENT_PEAK = 10 ** (-60 / 20);

/** Listens for `ms` and says which microphone it was and whether any sound came. Nothing leaves the browser. */
export async function testMicrophone(ms = 3_000): Promise<{ label: string; heard: boolean }> {
  const stream = await openMicrophone();
  const context = new AudioContext();
  try {
    void context.resume().catch(() => {});
    const analyser = context.createAnalyser();
    analyser.fftSize = 32_768; // ~0.7 s at 48 kHz: each reading overlaps the last, so no sound falls between two
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    let peak = 0;
    const end = performance.now() + ms;
    while (performance.now() < end) {
      await new Promise((resolve) => setTimeout(resolve, 200));
      analyser.getFloatTimeDomainData(samples);
      for (const sample of samples) peak = Math.max(peak, Math.abs(sample));
    }
    return { label: stream.getAudioTracks()[0]?.label ?? "", heard: peak >= SILENT_PEAK };
  } finally {
    stream.getTracks().forEach((track) => track.stop());
    context.close().catch(() => {});
  }
}
