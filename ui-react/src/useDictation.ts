import { useCallback, useEffect, useRef, useState } from "react";
import { transcribeSpeech } from "./apiClient";
import { openMicrophone, subscribeMicrophone } from "./microphone";
import { capturePcm, claimMicrophone, dropMicrophone, pcmToWav, RELEASE_TAIL_MS } from "./pcmCapture";

/**
 * Dictation for the chat's input (docs/adr/0033): a press listens, the next one stops and what was said becomes text
 * the user reads, edits and sends as any message. Nothing is sent to an agent here, and nothing is said back.
 *
 * `starting`: the microphone is opening. `silent`: no sound came (a muted or wrong microphone). `no-speech`: sound, but
 * no words. `no-mic`: the browser refused the microphone or has none. `error`: it could not be transcribed.
 */
export type DictationState = "idle" | "starting" | "listening" | "transcribing" | "silent" | "no-speech" | "no-mic" | "error";

/** Whether the microphone is in use or its recording is being transcribed. */
export function isDictating(state: DictationState): boolean {
  return state === "starting" || state === "listening" || state === "transcribing";
}

// agui-voice takes 120 s (MAX_DICTATION_SECONDS): stop a little before, the release tail included.
const MAX_RECORDING_MS = 118_000;
// Local Whisper on a CPU can take a while on a long recording, but the button must come back.
const TRANSCRIBE_TIMEOUT_MS = 90_000;

interface Recording {
  stream: MediaStream;
  context: AudioContext;
  chunks: Int16Array<ArrayBuffer>[];
  limit: ReturnType<typeof setTimeout>;
}

function stopRecording(recording: Recording): void {
  clearTimeout(recording.limit);
  recording.stream.getTracks().forEach((track) => track.stop());
  recording.context.close().catch(() => {});
}

export function useDictation(
  voiceUrl: string,
  scenarioSlug: string,
  accessToken: string | null,
  /** What was said, once transcribed. */
  onText: (text: string) => void,
) {
  const [state, setStateValue] = useState<DictationState>("idle");
  const [micName, setMicName] = useState("");
  const stateRef = useRef<DictationState>("idle");
  const recordingRef = useRef<Recording | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  // Each start gets a number: a cancelled one's microphone prompt or transcription, answered late, is ignored.
  const turnRef = useRef(0);
  const onTextRef = useRef(onText);
  useEffect(() => {
    onTextRef.current = onText;
  });

  const setState = useCallback((next: DictationState) => {
    stateRef.current = next;
    setStateValue(next);
  }, []);

  // Stable, so it also names this hook's hold on the microphone.
  const releaseRef = useRef<() => void>(() => {});

  /** Throw away what is being said or transcribed. */
  const cancel = useCallback(() => {
    turnRef.current++;
    requestRef.current?.abort();
    requestRef.current = null;
    if (recordingRef.current) stopRecording(recordingRef.current);
    recordingRef.current = null;
    dropMicrophone(releaseRef.current);
    setState("idle");
  }, [setState]);
  useEffect(() => {
    releaseRef.current = cancel;
  }, [cancel]);

  /** Stop listening and turn what was said into text. */
  const finish = useCallback(async () => {
    const recording = recordingRef.current;
    if (!recording || stateRef.current !== "listening") return;
    const turn = turnRef.current;
    setState("transcribing");
    await new Promise((resolve) => setTimeout(resolve, RELEASE_TAIL_MS));
    if (turn !== turnRef.current) return;
    recordingRef.current = null;
    stopRecording(recording);
    dropMicrophone(cancel);
    const request = new AbortController();
    requestRef.current = request;
    const timeout = setTimeout(() => request.abort(), TRANSCRIBE_TIMEOUT_MS);
    try {
      const heard = await transcribeSpeech(voiceUrl, scenarioSlug, pcmToWav(recording.chunks), accessToken, request.signal);
      if (turn !== turnRef.current) return;
      if (heard.text) {
        setState("idle");
        onTextRef.current(heard.text);
      } else {
        setState(heard.reason === "silent" ? "silent" : "no-speech");
      }
    } catch (err) {
      if (turn !== turnRef.current) return;
      console.error("Dictation: could not transcribe:", err);
      setState("error");
    } finally {
      clearTimeout(timeout);
      if (requestRef.current === request) requestRef.current = null;
    }
  }, [voiceUrl, scenarioSlug, accessToken, cancel, setState]);

  const start = useCallback(async () => {
    claimMicrophone(cancel); // ends a live voice session, or another chat's dictation
    const turn = ++turnRef.current;
    setState("starting");
    let stream: MediaStream;
    try {
      stream = await openMicrophone();
    } catch (err) {
      if (turn !== turnRef.current) return;
      console.error("Dictation: microphone access denied or unavailable:", err);
      dropMicrophone(cancel);
      setState("no-mic");
      return;
    }
    if (turn !== turnRef.current) {
      stream.getTracks().forEach((track) => track.stop()); // cancelled while the prompt was open
      return;
    }
    setMicName(stream.getAudioTracks()[0]?.label ?? "");
    const chunks: Int16Array<ArrayBuffer>[] = [];
    recordingRef.current = {
      stream,
      chunks,
      context: capturePcm(stream, (samples) => chunks.push(samples)),
      limit: setTimeout(() => void finish(), MAX_RECORDING_MS),
    };
    setState("listening");
  }, [cancel, finish, setState]);

  /** The mic button: listen, or stop and write it down. */
  const press = useCallback(() => {
    switch (stateRef.current) {
      case "starting":
      case "transcribing":
        cancel();
        break;
      case "listening":
        void finish();
        break;
      default:
        void start();
    }
  }, [cancel, finish, start]);

  // Esc discards, wherever the focus is.
  useEffect(() => {
    if (!isDictating(state)) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") cancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [state, cancel]);

  // Leaving the chat must not leave the microphone open.
  useEffect(() => cancel, [scenarioSlug, cancel]);
  // Another microphone was chosen (from the "no sound" notice, say): the notice is answered.
  useEffect(() => subscribeMicrophone(cancel), [cancel]);

  return { state, micName, press, finish, cancel };
}
