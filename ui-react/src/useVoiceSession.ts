import { useCallback, useEffect, useRef, useState } from "react";
import { randomUUID, type HttpAgent } from "@ag-ui/client";
import type { Context as AguiContext, InputContent, Message as AguiMessage, Tool as AguiTool } from "@ag-ui/core";
import { RTVIMessage } from "@pipecat-ai/client-js";
import { ProtobufFrameSerializer } from "@pipecat-ai/websocket-transport";
import { openMicrophone, subscribeMicrophone } from "./microphone";
import { capturePcm, claimMicrophone, dropMicrophone, RELEASE_TAIL_MS } from "./pcmCapture";

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
 * never ends the turn and nothing is heard between turns. Pressing while the answer is
 * being prepared or said only stops it (`cancel`): the user is not asked to talk again.
 * Holding the space bar then is the barge-in: its queued audio stops here at once, the
 * server interrupts the reply and the next turn opens. A connected session that nobody
 * uses hangs up by itself.
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
 * It also runs as a typed turn would: each press sends a `voice-turn` message with what
 * the chat sends a typed run (its tools, readable context, forwarded props and
 * transcript, from `turn.input()`), and a call the agent makes to one of those tools (a
 * screen action) comes back as a `frontend-tool-call` server message for `turn.onToolCall`
 * to carry out exactly as after a typed turn.
 *
 * Only one thing has the microphone app-wide (`claimMicrophone`): starting a session from
 * anywhere first ends whatever had it, another chat's session or a dictation.
 */
// `no-mic`: the browser refused the microphone or has none — not a connection failure, so it says so apart.
// `silent` and `no-speech`: still connected, but the last turn held no sound (a muted or wrong microphone) or no
// words, so no answer is coming.
export type VoiceSessionState =
  | "idle"
  | "connecting"
  | "ready"
  | "talking"
  | "thinking"
  | "speaking"
  | "silent"
  | "no-speech"
  | "error"
  | "no-mic";

/** Connected states: the session is open and the space bar talks. */
export function isVoiceConnected(state: VoiceSessionState): boolean {
  return (
    state === "ready" ||
    state === "talking" ||
    state === "thinking" ||
    state === "speaking" ||
    state === "silent" ||
    state === "no-speech"
  );
}

// If the server never answers "ready" (a crashed pipeline, a network issue, a
// protocol mismatch — see console for errors either way), don't spin on
// "Connecting…" forever: give up and surface a clear error instead.
const CONNECT_TIMEOUT_MS = 20_000;

// Matches AgentBridgeProcessor's TTS stage (Piper, see agui-voice/core/providers.py)
// — the wire protocol doesn't carry the playback sample rate (see
// ProtobufFrameSerializer.deserialize upstream, which discards it the same way).
const PLAYBACK_SAMPLE_RATE = 24_000;
const RTVI_PROTOCOL_VERSION = "2.1.0";

/** The RTVI `client-message` type agui-voice's core/push_to_talk.py listens for. */
export const PUSH_TO_TALK = "push-to-talk";
/** The RTVI `client-message` carrying a turn's tools, context, props and transcript (core/agent_bridge.py). */
export const VOICE_TURN = "voice-turn";
/** The RTVI `server-message` handing one of the chat's own tool calls back to run here (core/agent_bridge.py). */
export const FRONTEND_TOOL_CALL = "frontend-tool-call";
// The transcript a voice turn carries: its latest messages, text only (agent_bridge.py refuses more than 100, or
// over 512 KB in all, and the turn then runs without the chat's tools).
const VOICE_TRANSCRIPT_MESSAGES = 40;

/** What a spoken turn sends from the chat it is in: what a typed run of that chat sends. */
export interface VoiceTurnInput {
  tools: AguiTool[];
  context: AguiContext[];
  forwardedProps: Record<string, unknown>;
  messages: AguiMessage[];
}

/** A call the agent made, by voice, to one of the chat's own tools. */
export interface VoiceToolCall {
  id: string;
  name: string;
  arguments: string;
}

/** How a voice session takes part in its chat: what it sends with each turn, and who runs a screen action. */
export interface VoiceTurnBridge {
  input: () => VoiceTurnInput;
  onToolCall: (call: VoiceToolCall) => void;
}

/**
 * The chat's transcript as a voice turn carries it: user, assistant and tool messages only, a user message's
 * attachments reduced to its text (an image is seen in the typed turn it came with), and only the latest ones,
 * starting at a user message so no tool result is left without its call.
 */
export function transcriptForVoice(messages: readonly AguiMessage[]): AguiMessage[] {
  const kept = messages
    .filter((m) => m.role === "user" || m.role === "assistant" || m.role === "tool")
    .map((m) => {
      if (m.role !== "user" || typeof m.content === "string") return m;
      const text = (m.content as InputContent[])
        .filter((c): c is InputContent & { type: "text" } => c.type === "text")
        .map((c) => c.text)
        .join("\n");
      return { ...m, content: text };
    })
    .slice(-VOICE_TRANSCRIPT_MESSAGES);
  const start = kept.findIndex((m) => m.role === "user");
  return start < 0 ? [] : kept.slice(start);
}
// The server ends a turn itself past 60 s of audio (DEFAULT_MAX_TURN_SECS); the client stops at the same point.
const MAX_TURN_MS = 60_000;
// No agent run after a turn (silence, nothing transcribed): give the button back.
const THINKING_TIMEOUT_MS = 30_000;
// The agent has started (bot-llm-started) but not answered yet: a cold prompt cache plus a slow model took 30 s once,
// and giving the button back then made the answer arrive later, unannounced. Still bounded, for a run that hangs.
const AGENT_RUN_TIMEOUT_MS = 120_000;
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
  stop: () => void;
  dispose: () => void;
}

function stopSession(session: VoiceSession): void {
  session.mediaStream.getTracks().forEach((track) => track.stop());
  session.audioContext.close().catch(() => {});
  session.playbackContext.close().catch(() => {});
  if (session.ws.readyState === WebSocket.OPEN || session.ws.readyState === WebSocket.CONNECTING) {
    // 1000: a normal close, so the owner's onclose reads it as "ended", not as a failure.
    session.ws.close(1000, "voice mode ended");
  }
}

export function useVoiceSession(
  agent: HttpAgent,
  voiceUrl: string,
  scenarioSlug: string,
  accessToken: string | null,
  turn?: VoiceTurnBridge,
) {
  const [state, setStateValue] = useState<VoiceSessionState>("idle");
  // The microphone in use, named when a turn was silent: it is often not the one the user expects.
  const [micName, setMicName] = useState("");
  // Read when a turn starts or a screen action arrives: the chat's latest tools, screen and handlers.
  const turnRef = useRef(turn);
  useEffect(() => {
    turnRef.current = turn;
  });
  // The callbacks below run from socket and key events: they read the state from here, not from a stale render.
  const stateRef = useRef<VoiceSessionState>("idle");
  const sessionRef = useRef<VoiceSession | null>(null);
  const talkRef = useRef<TalkControls | null>(null);
  // A press that connects means "talk as soon as it is ready".
  const attemptRef = useRef(0); // a connection given up while the mic prompt was open must not go on when it is answered
  const talkOnReadyRef = useRef(false);
  const botTextRef = useRef("");
  const nextPlaybackTimeRef = useRef(0);
  const scheduledSourcesRef = useRef<AudioBufferSourceNode[]>([]);

  const setState = useCallback((next: VoiceSessionState) => {
    stateRef.current = next;
    setStateValue(next);
  }, []);

  // Stable, so it also names this hook's hold on the microphone.
  const releaseRef = useRef<() => void>(() => {});

  /** End voice mode: close the socket, release the mic, forget the turn. */
  const hangUp = useCallback(
    (next: VoiceSessionState = "idle") => {
      attemptRef.current++;
      talkRef.current?.dispose();
      talkRef.current = null;
      talkOnReadyRef.current = false;
      const session = sessionRef.current;
      sessionRef.current = null;
      if (session) stopSession(session);
      dropMicrophone(releaseRef.current);
      setState(next);
    },
    [setState],
  );
  const end = useCallback(() => hangUp(), [hangUp]);
  useEffect(() => {
    releaseRef.current = end;
  }, [end]);

  const connect = useCallback(async () => {
    if (sessionRef.current) return;
    claimMicrophone(end); // only one user of the microphone app-wide
    const attempt = ++attemptRef.current;
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
      mediaStream = await openMicrophone();
    } catch (err) {
      console.error("Voice session: microphone access denied or unavailable:", err);
      if (attempt !== attemptRef.current) return;
      hangUp("no-mic");
      return;
    }
    // Hung up (or another chat took the voice session) while the mic prompt was open.
    if (attempt !== attemptRef.current) {
      mediaStream.getTracks().forEach((track) => track.stop());
      return;
    }

    setMicName(mediaStream.getAudioTracks()[0]?.label ?? "");

    const playbackContext = new AudioContext({ sampleRate: PLAYBACK_SAMPLE_RATE });
    // Fire-and-forget: MicButton's synchronous unlockAudioPlayback() is what satisfies the browser's
    // autoplay-gesture requirement — this is a safety net, so a slow resume can never block connect().
    playbackContext.resume().catch(() => {});
    const ws = new WebSocket(wsUrl.toString());
    ws.binaryType = "blob";

    // Mic audio goes out only while this is true: between a press and the end of its release tail.
    let sendingAudio = false;
    const audioContext = capturePcm(mediaStream, (samples) => {
      if (!sendingAudio || ws.readyState !== WebSocket.OPEN) return;
      ws.send(serializer.serializeAudio(samples.buffer, audioContext.sampleRate, 1) as unknown as ArrayBuffer);
    });

    const session: VoiceSession = { ws, audioContext, playbackContext, mediaStream };
    sessionRef.current = session;
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
    // After the user's stop: what is still on its way from that turn is dropped, until the next press.
    let stopped = false;
    const sendTalking = (talking: boolean, cancel = false) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      const message = new RTVIMessage("client-message", { t: PUSH_TO_TALK, d: { talking, ...(cancel ? { cancel } : {}) } });
      ws.send(serializer.serializeMessage(message) as unknown as ArrayBuffer);
    };
    // What a typed run of this chat would send, taken as the user starts talking: the same tools, screen and transcript.
    const sendTurnInput = () => {
      const bridge = turnRef.current;
      if (!bridge || ws.readyState !== WebSocket.OPEN) return;
      let input: VoiceTurnInput;
      try {
        input = bridge.input();
      } catch (err) {
        console.error("Voice session: could not read the chat's tools and context:", err);
        return;
      }
      const message = new RTVIMessage("client-message", { t: VOICE_TURN, d: input });
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
    const becomeReady = (next: "ready" | "silent" | "no-speech" = "ready") => {
      clearTimer("thinking");
      clearTimer("idle");
      setState(next);
      timers.idle = setTimeout(() => {
        if (stateRef.current === next) hangUp();
      }, IDLE_HANGUP_MS);
    };
    const controls: TalkControls = {
      begin() {
        flushTail();
        clearTimer("thinking");
        clearTimer("idle");
        clearTimer("max");
        stopped = false;
        clearScheduledPlayback(); // barge-in, heard at once; the server interrupts the reply too
        sendTalking(true);
        sendTurnInput();
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
      stop() {
        clearTimer("tail");
        clearTimer("max");
        stopped = true;
        sendingAudio = false;
        clearScheduledPlayback();
        sendTalking(false, true);
        becomeReady();
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
      if (wasActive) {
        sessionRef.current = null;
        dropMicrophone(end);
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
        // While the user talks, or after their stop, the old reply is over: drop its audio still in flight.
        if (stateRef.current !== "talking" && !stopped) playAudioChunk(parsed.audio);
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
          const data = message.data as
            | ({ type?: string; talking?: boolean; reason?: string } & Partial<VoiceToolCall>)
            | undefined;
          if (data?.type === PUSH_TO_TALK && (data.reason === "silent" || data.reason === "no-speech")) {
            // Only for the turn just sent: if the user is already talking again, it is about the previous one.
            if (stateRef.current === "thinking") becomeReady(data.reason);
          } else if (data?.type === PUSH_TO_TALK && data.talking === false && stateRef.current === "talking") {
            // The server ended an over-long turn itself (core/push_to_talk.py's limit).
            clearTimer("max");
            clearTimer("tail");
            sendingAudio = false;
            setState("thinking");
            waitForReply(THINKING_TIMEOUT_MS);
          } else if (
            data?.type === FRONTEND_TOOL_CALL &&
            !stopped &&
            typeof data.id === "string" &&
            typeof data.name === "string" &&
            typeof data.arguments === "string"
          ) {
            // A screen action the agent took by voice: the chat carries it out as after a typed turn.
            turnRef.current?.onToolCall({ id: data.id, name: data.name, arguments: data.arguments });
          }
          break;
        }
        case "user-transcription": {
          const data = message.data as { text?: string; final?: boolean } | undefined;
          if (data?.final && data.text?.trim() && !stopped) {
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
          // The turn was heard and the agent is working on it: wait for its answer, not for the 30 s first step.
          if (!stopped && (stateRef.current === "silent" || stateRef.current === "no-speech")) {
            clearTimer("idle"); // something was transcribed after all
            setState("thinking");
          }
          if (stateRef.current === "thinking") waitForReply(AGENT_RUN_TIMEOUT_MS);
          break;
        case "bot-llm-text": {
          const data = message.data as { text?: string } | undefined;
          if (data?.text && !stopped) botTextRef.current += data.text;
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
          if (!stopped && (stateRef.current === "thinking" || stateRef.current === "ready")) {
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
      if (sessionRef.current !== session) return;
      console.error(`Voice session: no "ready" response from the server within ${CONNECT_TIMEOUT_MS / 1000}s — giving up.`);
      hangUp("error");
    }, CONNECT_TIMEOUT_MS);
  }, [agent, voiceUrl, scenarioSlug, accessToken, end, hangUp, setState]);

  /** The voice button: connect and talk, send the turn, or stop the answer. */
  const press = useCallback(() => {
    switch (stateRef.current) {
      case "idle":
      case "error":
      case "no-mic":
        talkOnReadyRef.current = true;
        void connect();
        break;
      case "connecting":
        hangUp();
        break;
      case "talking":
        talkRef.current?.end();
        break;
      case "thinking":
      case "speaking":
        talkRef.current?.stop();
        break;
      default: // ready, silent, no-speech
        talkRef.current?.begin();
    }
  }, [connect, hangUp]);

  /** The space bar going down: talk, interrupting an answer if there is one. */
  const talk = useCallback(() => {
    if (stateRef.current !== "talking") talkRef.current?.begin();
  }, []);
  /** The space bar coming up: send the turn it held open. */
  const release = useCallback(() => {
    if (stateRef.current === "talking") talkRef.current?.end();
    else if (stateRef.current === "connecting") talkOnReadyRef.current = false;
  }, []);

  // Leaving this conversation — switching scenarios (new scenarioSlug), opening another
  // conversation (new threadId: the session is bound to the old one) or unmounting
  // entirely — must stop any live mic/audio, not leave it running in the background.
  useEffect(() => () => hangUp(), [scenarioSlug, agent.threadId, hangUp]);

  // Another microphone was chosen: the open session listens to the old one, so the next press connects with the new.
  useEffect(
    () =>
      subscribeMicrophone(() => {
        if (sessionRef.current || stateRef.current === "connecting") hangUp();
      }),
    [hangUp],
  );

  return { state, micName, press, talk, release, end };
}
