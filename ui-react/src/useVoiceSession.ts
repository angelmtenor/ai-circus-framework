import { useCallback, useEffect, useRef, useState } from "react";
import { randomUUID, type HttpAgent } from "@ag-ui/client";
import { RTVIMessage } from "@pipecat-ai/client-js";
import { ProtobufFrameSerializer } from "@pipecat-ai/websocket-transport";

/**
 * Push-to-talk voice conversation for one scenario (docs/adr/0033), talking directly
 * to agui-voice's `GET /ws/{scenarioSlug}` Pipecat pipeline (see
 * `services/agui-voice/src/agui_voice/api/ws.py`) over a plain WebSocket.
 *
 * The user, not a voice-activity detector, bounds each turn: `press()` starts talking
 * (connecting first if needed) and `press()` again — or `release()`, when the space bar
 * is let go — sends it. Each edge is an RTVI `client-message` of type `push-to-talk`
 * (`{talking: true|false}`) that agui-voice's `PushToTalkProcessor` turns into the
 * frames that bound Whisper's segment. Mic audio is streamed only while talking (plus a
 * short tail after the release, so the last word is not cut), so a pause mid-sentence
 * never ends the turn and nothing is heard between turns. Pressing while the bot speaks
 * is the barge-in: its queued audio stops here at once and the server interrupts the
 * reply. A connected session that nobody uses hangs up by itself.
 *
 * This does NOT use `@pipecat-ai/client-js`'s `PipecatClient` — its bundled
 * `WavRecorder`/`WavMediaManager` audio-device layer was found to hang silently
 * (confirmed via Chrome's "pause on caught exceptions" debugger and direct patching
 * of `getUserMedia`/`AudioContext.resume`/`AudioWorklet.addModule`: none of them
 * ever got called after the WebSocket opened — the hang is inside the SDK's own
 * device-setup code, reproduced identically across page reloads, Incognito, and
 * Edge). Everything else about the protocol is unchanged: this still speaks the
 * exact same RTVI-over-Protobuf wire format the server expects, reusing
 * `@pipecat-ai/websocket-transport`'s own `ProtobufFrameSerializer` for encoding —
 * only the browser-side mic capture/playback (a few dozen lines of plain Web Audio
 * API) is hand-rolled instead of going through the SDK's device manager.
 *
 * A voice turn lands in the *same* `agent.messages` transcript a typed turn would —
 * `user-transcription`/`bot-llm-started`/`bot-llm-text`/`bot-llm-stopped` map 1:1
 * onto the server's `AgentBridgeProcessor` frame sequence (TranscriptionFrame ->
 * LLMFullResponseStartFrame -> TextFrame deltas -> LLMFullResponseEndFrame), so
 * ChatPanel's existing `onMessagesChanged` subscription renders it identically —
 * no separate voice transcript state, no CopilotKit changes.
 *
 * Only one live session is allowed app-wide at a time: `activeSession` is a
 * module-level singleton (not per-hook-instance state), so starting a session from
 * anywhere first tears down whatever was already running.
 */
export type VoiceSessionState = "idle" | "connecting" | "ready" | "talking" | "thinking" | "speaking" | "error";

/** Connected states: the session is open and the space bar talks. */
export function isVoiceConnected(state: VoiceSessionState): boolean {
  return state === "ready" || state === "talking" || state === "thinking" || state === "speaking";
}

// If the server never answers "ready" (a crashed pipeline, a network issue, a
// protocol mismatch — see console for errors either way), don't spin on
// "Connecting…" forever: give up and surface a clear error instead.
const CONNECT_TIMEOUT_MS = 20_000;

// Matches AgentBridgeProcessor's TTS stage (Piper, see agui-voice/core/providers.py)
// — the wire protocol doesn't carry the playback sample rate (see
// ProtobufFrameSerializer.deserialize upstream, which discards it the same way).
const PLAYBACK_SAMPLE_RATE = 24_000;
// Must match the server pipeline's fixed STT rate (Whisper expects 16kHz) — the
// transport never resamples incoming audio, it trusts whatever rate each frame
// claims (see BaseInputTransport.push_audio_frame in pipecat), so sending the mic's
// native device rate (44.1/48kHz) here silently breaks STT without any error.
// Requesting this rate on the AudioContext makes the browser resample the mic
// stream itself before onaudioprocess ever sees it.
const CAPTURE_SAMPLE_RATE = 16_000;
const CAPTURE_BUFFER_SIZE = 4096;
const RTVI_PROTOCOL_VERSION = "2.1.0";

/** The RTVI `client-message` type agui-voice's core/push_to_talk.py listens for. */
export const PUSH_TO_TALK = "push-to-talk";
// The server ends a turn itself past 60 s of audio (DEFAULT_MAX_TURN_SECS); the client stops at the same point.
const MAX_TURN_MS = 60_000;
// Keep streaming this long after the release: the capture buffer (4096 samples at
// 16 kHz = 256 ms) still holds the last word, and the stop must not overtake it.
const RELEASE_TAIL_MS = 320;
// No transcript or reply after a turn (silence, or a failed run): give the button back.
const THINKING_TIMEOUT_MS = 30_000;
// The reply's text is done but no audio started (TTS failed or had nothing to say).
const AFTER_REPLY_MS = 4_000;
// Connected but unused this long: hang up, which frees the server's pipeline and the microphone.
const IDLE_HANGUP_MS = 180_000;

interface VoiceSession {
  ws: WebSocket;
  audioContext: AudioContext;
  playbackContext: AudioContext;
  mediaStream: MediaStream;
}

/** The current session's turn controls; they close over its socket, timers and audio. */
interface TalkControls {
  begin: () => void;
  end: () => void;
  dispose: () => void;
}

let activeSession: VoiceSession | null = null;

function stopSession(session: VoiceSession): void {
  session.mediaStream.getTracks().forEach((track) => track.stop());
  session.audioContext.close().catch(() => {});
  session.playbackContext.close().catch(() => {});
  if (session.ws.readyState === WebSocket.OPEN || session.ws.readyState === WebSocket.CONNECTING) {
    // 1000: a normal close, so the owner's onclose reads it as "ended", not as a failure.
    session.ws.close(1000, "voice mode ended");
  }
}

function disconnectActiveSession(): void {
  if (activeSession) {
    stopSession(activeSession);
    activeSession = null;
  }
}

export function useVoiceSession(
  agent: HttpAgent,
  voiceUrl: string,
  scenarioSlug: string,
  accessToken: string | null,
) {
  const [state, setStateValue] = useState<VoiceSessionState>("idle");
  // The callbacks below run from socket and key events: they read the state from here, not from a stale render.
  const stateRef = useRef<VoiceSessionState>("idle");
  const sessionRef = useRef<VoiceSession | null>(null);
  const talkRef = useRef<TalkControls | null>(null);
  // A press while connecting means "talk as soon as it is ready"; a second press takes that back.
  const talkOnReadyRef = useRef(false);
  const botTextRef = useRef("");
  const nextPlaybackTimeRef = useRef(0);
  const scheduledSourcesRef = useRef<AudioBufferSourceNode[]>([]);

  const setState = useCallback((next: VoiceSessionState) => {
    stateRef.current = next;
    setStateValue(next);
  }, []);

  /** End voice mode: close the socket, release the mic, forget the turn. */
  const hangUp = useCallback(
    (next: VoiceSessionState = "idle") => {
      talkRef.current?.dispose();
      talkRef.current = null;
      talkOnReadyRef.current = false;
      const session = sessionRef.current;
      sessionRef.current = null;
      if (session) {
        if (activeSession === session) activeSession = null;
        stopSession(session);
      }
      setState(next);
    },
    [setState],
  );

  const connect = useCallback(async () => {
    if (sessionRef.current) return;
    disconnectActiveSession(); // only one live voice session app-wide
    setState("connecting");

    let readyTimer: ReturnType<typeof setTimeout> | null = null;
    const serializer = new ProtobufFrameSerializer();

    const wsUrl = new URL(`/ws/${scenarioSlug}`, voiceUrl);
    wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";
    if (accessToken) wsUrl.searchParams.set("token", accessToken);
    // The open conversation: the agent serves a run only for one the caller owns, and keeps the spoken turns in it.
    wsUrl.searchParams.set("thread_id", agent.threadId);

    let mediaStream: MediaStream;
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      console.error("Voice session: microphone access denied or unavailable:", err);
      talkOnReadyRef.current = false;
      setState("error");
      return;
    }
    // Hung up (or another chat took the voice session) while the mic prompt was open.
    if (stateRef.current !== "connecting") {
      mediaStream.getTracks().forEach((track) => track.stop());
      return;
    }

    const audioContext = new AudioContext({ sampleRate: CAPTURE_SAMPLE_RATE });
    const playbackContext = new AudioContext({ sampleRate: PLAYBACK_SAMPLE_RATE });
    // Fire-and-forget: MicButton's synchronous unlockAudioPlayback() (run inside the
    // click handler, before any of this async work) is what actually satisfies the
    // browser's autoplay-gesture requirement — these calls are just a safety net,
    // not awaited, so a slow/suspended resume can never block the rest of connect().
    audioContext.resume().catch(() => {});
    playbackContext.resume().catch(() => {});
    const ws = new WebSocket(wsUrl.toString());
    ws.binaryType = "blob";

    const session: VoiceSession = { ws, audioContext, playbackContext, mediaStream };
    sessionRef.current = session;
    activeSession = session;
    nextPlaybackTimeRef.current = 0;
    scheduledSourcesRef.current = [];

    const clearScheduledPlayback = () => {
      for (const source of scheduledSourcesRef.current) {
        try {
          source.stop();
        } catch {
          // Already stopped/finished — fine.
        }
      }
      scheduledSourcesRef.current = [];
      nextPlaybackTimeRef.current = playbackContext.currentTime;
    };

    const playAudioChunk = (samples: Int16Array) => {
      const float32 = new Float32Array(samples.length);
      for (let i = 0; i < samples.length; i++) float32[i] = samples[i] / 32768;
      const buffer = playbackContext.createBuffer(1, float32.length, PLAYBACK_SAMPLE_RATE);
      buffer.copyToChannel(float32, 0);
      const source = playbackContext.createBufferSource();
      source.buffer = buffer;
      source.connect(playbackContext.destination);
      const startAt = Math.max(nextPlaybackTimeRef.current, playbackContext.currentTime);
      source.start(startAt);
      nextPlaybackTimeRef.current = startAt + buffer.duration;
      scheduledSourcesRef.current.push(source);
      source.onended = () => {
        scheduledSourcesRef.current = scheduledSourcesRef.current.filter((s) => s !== source);
      };
    };

    // Mic audio goes out only while this is true: between a press and the end of its release tail.
    let sendingAudio = false;
    const sourceNode = audioContext.createMediaStreamSource(mediaStream);
    // ScriptProcessorNode is deprecated in favor of AudioWorklet, but it needs no
    // async module loading (no blob-URL step to hang on) and its synchronous
    // callback model is simpler and sufficient here — deliberate tradeoff after the
    // AudioWorklet-based SDK path above proved unreliable in this environment.
    const processor = audioContext.createScriptProcessor(CAPTURE_BUFFER_SIZE, 1, 1);
    processor.onaudioprocess = (event) => {
      if (!sendingAudio || ws.readyState !== WebSocket.OPEN) return;
      const float32 = event.inputBuffer.getChannelData(0);
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        const clamped = Math.max(-1, Math.min(1, float32[i]));
        int16[i] = clamped < 0 ? clamped * 32768 : clamped * 32767;
      }
      ws.send(serializer.serializeAudio(int16.buffer, audioContext.sampleRate, 1) as unknown as ArrayBuffer);
    };
    sourceNode.connect(processor);
    // ScriptProcessorNode only fires onaudioprocess while connected to a
    // destination — route it through a zero-gain node instead of the real
    // destination, or the user's own mic input would play back through their
    // speakers (audible echo/feedback) instead of just being captured.
    const silentSink = audioContext.createGain();
    silentSink.gain.value = 0;
    processor.connect(silentSink);
    silentSink.connect(audioContext.destination);

    // ── The turn: press, release, and the timers that keep every state bounded ──
    const timers: Record<"tail" | "max" | "thinking" | "idle", ReturnType<typeof setTimeout> | null> = {
      tail: null,
      max: null,
      thinking: null,
      idle: null,
    };
    const clearTimer = (name: keyof typeof timers) => {
      const timer = timers[name];
      if (timer) clearTimeout(timer);
      timers[name] = null;
    };
    const sendTalking = (talking: boolean) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      const message = new RTVIMessage("client-message", { t: PUSH_TO_TALK, d: { talking } });
      ws.send(serializer.serializeMessage(message) as unknown as ArrayBuffer);
    };
    // A press during the previous release's tail closes that turn first, at once.
    const flushTail = () => {
      if (!timers.tail) return;
      clearTimer("tail");
      sendingAudio = false;
      sendTalking(false);
    };
    const waitForReply = (ms: number) => {
      clearTimer("thinking");
      timers.thinking = setTimeout(() => {
        if (stateRef.current === "thinking") becomeReady();
      }, ms);
    };
    const becomeReady = () => {
      clearTimer("thinking");
      clearTimer("idle");
      setState("ready");
      timers.idle = setTimeout(() => {
        if (stateRef.current === "ready") hangUp();
      }, IDLE_HANGUP_MS);
    };
    const controls: TalkControls = {
      begin() {
        flushTail();
        clearTimer("thinking");
        clearTimer("idle");
        clearTimer("max");
        clearScheduledPlayback(); // barge-in, heard at once; the server interrupts the reply too
        sendTalking(true);
        sendingAudio = true;
        setState("talking");
        timers.max = setTimeout(() => controls.end(), MAX_TURN_MS);
      },
      end() {
        if (stateRef.current !== "talking") return;
        clearTimer("max");
        setState("thinking");
        timers.tail = setTimeout(() => {
          timers.tail = null;
          sendingAudio = false;
          sendTalking(false);
        }, RELEASE_TAIL_MS);
        waitForReply(THINKING_TIMEOUT_MS);
      },
      dispose() {
        for (const name of Object.keys(timers) as (keyof typeof timers)[]) clearTimer(name);
        sendingAudio = false;
      },
    };
    talkRef.current = controls;

    ws.onopen = () => {
      const clientReady = new RTVIMessage("client-ready", {
        version: RTVI_PROTOCOL_VERSION,
        about: { library: "ai-circus-custom-voice-client", library_version: "2.0.0" },
      });
      ws.send(serializer.serializeMessage(clientReady) as unknown as ArrayBuffer);
    };

    ws.onerror = () => {
      console.error("Voice session: WebSocket error.");
    };

    ws.onclose = (event) => {
      if (readyTimer) clearTimeout(readyTimer);
      controls.dispose();
      const wasActive = sessionRef.current === session;
      if (activeSession === session) activeSession = null;
      if (wasActive) {
        sessionRef.current = null;
        talkRef.current = null;
        talkOnReadyRef.current = false;
        // The server closed it (or the network did): release the mic and the audio contexts too.
        stopSession(session);
        if (event.code !== 1000) console.error(`Voice session: connection closed (code ${event.code}) ${event.reason}`);
        setState(event.code === 1000 ? "idle" : "error");
      }
    };

    ws.onmessage = async (event: MessageEvent<Blob | string>) => {
      if (typeof event.data === "string") return;
      let parsed: Awaited<ReturnType<ProtobufFrameSerializer["deserialize"]>>;
      try {
        parsed = await serializer.deserialize(event.data);
      } catch (err) {
        console.error("Voice session: failed to parse server message:", err);
        return;
      }
      if (parsed.type === "audio") {
        // While the user talks, the old reply is over: drop its audio still in flight.
        if (stateRef.current !== "talking") playAudioChunk(parsed.audio);
        return;
      }
      const message = parsed.message as { type?: string; data?: Record<string, unknown> };
      switch (message.type) {
        case "bot-ready":
          if (readyTimer) clearTimeout(readyTimer);
          if (talkOnReadyRef.current) {
            talkOnReadyRef.current = false;
            controls.begin();
          } else {
            becomeReady();
          }
          break;
        case "server-message": {
          // The server ended an over-long turn itself (core/push_to_talk.py's limit).
          const data = message.data as { type?: string; talking?: boolean } | undefined;
          if (data?.type === PUSH_TO_TALK && data.talking === false && stateRef.current === "talking") {
            clearTimer("max");
            clearTimer("tail");
            sendingAudio = false;
            setState("thinking");
            waitForReply(THINKING_TIMEOUT_MS);
          }
          break;
        }
        case "user-transcription": {
          const data = message.data as { text?: string; final?: boolean } | undefined;
          if (data?.final && data.text?.trim()) {
            agent.addMessage({ id: randomUUID(), role: "user", content: data.text });
          }
          break;
        }
        // Maps 1:1 onto AgentBridgeProcessor's LLMFullResponseStartFrame -> TextFrame
        // deltas -> LLMFullResponseEndFrame (see agent_bridge.py) — accumulate
        // deltas between started/stopped into one assistant message, same shape a
        // typed turn's streamed reply collapses to once `agent.messages` settles.
        case "bot-llm-started":
          botTextRef.current = "";
          break;
        case "bot-llm-text": {
          const data = message.data as { text?: string } | undefined;
          if (data?.text) botTextRef.current += data.text;
          break;
        }
        case "bot-llm-stopped":
          if (botTextRef.current.trim()) {
            agent.addMessage({ id: randomUUID(), role: "assistant", content: botTextRef.current });
          }
          botTextRef.current = "";
          if (stateRef.current === "thinking") waitForReply(AFTER_REPLY_MS);
          break;
        case "bot-started-speaking":
          if (stateRef.current === "thinking" || stateRef.current === "ready") {
            clearTimer("thinking");
            clearTimer("idle");
            setState("speaking");
          }
          break;
        case "bot-stopped-speaking":
          if (stateRef.current === "speaking") becomeReady();
          break;
        case "bot-interrupted":
          clearScheduledPlayback();
          break;
        default:
          break;
      }
    };

    readyTimer = setTimeout(() => {
      console.error(`Voice session: no "ready" response from the server within ${CONNECT_TIMEOUT_MS / 1000}s — giving up.`);
      hangUp("error");
    }, CONNECT_TIMEOUT_MS);
  }, [agent, voiceUrl, scenarioSlug, accessToken, hangUp, setState]);

  /** The mic button, or the space bar going down: connect and talk, send the turn, or interrupt and talk. */
  const press = useCallback(() => {
    switch (stateRef.current) {
      case "idle":
      case "error":
        talkOnReadyRef.current = true;
        void connect();
        break;
      case "connecting":
        talkOnReadyRef.current = !talkOnReadyRef.current;
        break;
      case "talking":
        talkRef.current?.end();
        break;
      default: // ready, thinking, speaking
        talkRef.current?.begin();
    }
  }, [connect]);

  /** The space bar coming up: send the turn it held open. */
  const release = useCallback(() => {
    if (stateRef.current === "talking") talkRef.current?.end();
    else if (stateRef.current === "connecting") talkOnReadyRef.current = false;
  }, []);

  const end = useCallback(() => hangUp(), [hangUp]);

  // Leaving this conversation — switching scenarios (new scenarioSlug), opening another
  // conversation (new threadId: the session is bound to the old one) or unmounting
  // entirely — must stop any live mic/audio, not leave it running in the background.
  useEffect(() => () => hangUp(), [scenarioSlug, agent.threadId, hangUp]);

  return { state, press, release, end };
}
