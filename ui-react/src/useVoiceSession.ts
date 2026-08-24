import { useCallback, useEffect, useRef, useState } from "react";
import { randomUUID, type HttpAgent } from "@ag-ui/client";
import { RTVIMessage } from "@pipecat-ai/client-js";
import { ProtobufFrameSerializer } from "@pipecat-ai/websocket-transport";

/**
 * Live mic conversation (Grok/ChatGPT-style voice mode) for one scenario, talking
 * directly to agui-voice's `GET /ws/{scenarioSlug}` Pipecat pipeline (see
 * `services/agui-voice/src/agui_voice/api/ws.py`) over a plain WebSocket.
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
 * Turn-taking/barge-in is entirely server-side (Pipecat's own VAD-driven
 * turn-tracking, see agui-voice's `PipelineWorker`) — this client only has to
 * stream mic audio continuously once ready, and stop scheduled playback audio when
 * the server reports an interruption.
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
export type VoiceSessionState = "idle" | "connecting" | "listening" | "speaking" | "error";

// If the server never answers "ready" (a crashed pipeline, a network issue, a
// protocol mismatch — see console for errors either way), don't spin on
// "Connecting…" forever: give up and surface a clear error instead.
const CONNECT_TIMEOUT_MS = 20_000;

// Matches AgentBridgeProcessor's TTS stage (Piper, see agui-voice/core/providers.py)
// — the wire protocol doesn't carry the playback sample rate (see
// ProtobufFrameSerializer.deserialize upstream, which discards it the same way).
const PLAYBACK_SAMPLE_RATE = 24_000;
// Must match the server pipeline's fixed VAD/STT rate (Silero VAD + Whisper both
// expect 16kHz) — the transport never resamples incoming audio, it trusts whatever
// rate each frame claims (see BaseInputTransport.push_audio_frame in pipecat), so
// sending the mic's native device rate (44.1/48kHz) here silently breaks VAD/STT
// without any error. Requesting this rate on the AudioContext makes the browser
// resample the mic stream itself before onaudioprocess ever sees it.
const CAPTURE_SAMPLE_RATE = 16_000;
const CAPTURE_BUFFER_SIZE = 4096;
const RTVI_PROTOCOL_VERSION = "2.1.0";

interface VoiceSession {
  ws: WebSocket;
  audioContext: AudioContext;
  playbackContext: AudioContext;
  mediaStream: MediaStream;
}

let activeSession: VoiceSession | null = null;

function stopSession(session: VoiceSession): void {
  session.mediaStream.getTracks().forEach((track) => track.stop());
  session.audioContext.close().catch(() => {});
  session.playbackContext.close().catch(() => {});
  if (session.ws.readyState === WebSocket.OPEN || session.ws.readyState === WebSocket.CONNECTING) {
    session.ws.close();
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
  const [state, setState] = useState<VoiceSessionState>("idle");
  const sessionRef = useRef<VoiceSession | null>(null);
  const botTextRef = useRef("");
  const nextPlaybackTimeRef = useRef(0);
  const scheduledSourcesRef = useRef<AudioBufferSourceNode[]>([]);

  const stop = useCallback(() => {
    const session = sessionRef.current;
    sessionRef.current = null;
    if (activeSession === session && session) {
      stopSession(session);
      activeSession = null;
    }
    setState("idle");
  }, []);

  const start = useCallback(async () => {
    if (sessionRef.current) return;
    disconnectActiveSession(); // only one live voice session app-wide
    setState("connecting");

    let readyTimer: ReturnType<typeof setTimeout> | null = null;
    const serializer = new ProtobufFrameSerializer();

    const wsUrl = new URL(`/ws/${scenarioSlug}`, voiceUrl);
    wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";
    if (accessToken) wsUrl.searchParams.set("token", accessToken);

    let mediaStream: MediaStream;
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      console.error("Voice session: microphone access denied or unavailable:", err);
      setState("error");
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

    ws.onopen = () => {
      const clientReady = new RTVIMessage("client-ready", {
        version: RTVI_PROTOCOL_VERSION,
        about: { library: "ai-circus-custom-voice-client", library_version: "1.0.0" },
      });
      ws.send(serializer.serializeMessage(clientReady) as unknown as ArrayBuffer);
    };

    ws.onerror = () => {
      console.error("Voice session: WebSocket error.");
    };

    ws.onclose = (event) => {
      if (readyTimer) clearTimeout(readyTimer);
      const wasActive = sessionRef.current === session;
      sessionRef.current = null;
      if (activeSession === session) activeSession = null;
      if (wasActive) {
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
        playAudioChunk(parsed.audio);
        return;
      }
      const message = parsed.message as { type?: string; data?: Record<string, unknown> };
      switch (message.type) {
        case "bot-ready":
          if (readyTimer) clearTimeout(readyTimer);
          sendingAudio = true;
          setState("listening");
          break;
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
          break;
        case "bot-started-speaking":
          setState("speaking");
          break;
        case "bot-stopped-speaking":
          setState("listening");
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
      stop();
    }, CONNECT_TIMEOUT_MS);
  }, [agent, voiceUrl, scenarioSlug, accessToken, stop]);

  // Leaving this conversation — switching scenarios (new scenarioSlug) or unmounting
  // entirely — must stop any live mic/audio, not leave it running in the background.
  useEffect(() => {
    return () => {
      const session = sessionRef.current;
      sessionRef.current = null;
      if (session) {
        if (activeSession === session) activeSession = null;
        stopSession(session);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenarioSlug]);

  return { state, start, stop };
}
