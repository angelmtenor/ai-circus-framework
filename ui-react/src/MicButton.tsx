import { useEffect, useRef, useState } from "react";
import type { HttpAgent } from "@ag-ui/client";
import { voiceProviders, type VoiceProviderGroup, type VoiceProviders } from "./apiClient";
import { refreshServiceAvailability, SERVICES, useServiceAvailability, type ServiceKey } from "./serviceAvailability";
import { isVoiceConnected, useVoiceSession, type VoiceSessionState, type VoiceTurnBridge } from "./useVoiceSession";

const LABELS: Record<VoiceSessionState, string> = {
  idle: "Talk: press to start, press again to send. Once voice is on, you can also hold the space bar",
  connecting: "Connecting… talk when the button turns red (press again to connect without talking)",
  ready: "Press to talk, or hold the space bar",
  talking: "Talking — press again, or let go of the space bar, to send",
  thinking: "Sent — waiting for the answer (press to talk again)",
  speaking: "Answering — press, or hold the space bar, to interrupt and talk",
  error: "Voice connection failed — press to retry",
  "no-mic":
    "No microphone: the browser blocked it or found none. Allow the microphone for this site (the icon at the left of the address bar), then press to retry",
};

const ICONS: Record<VoiceSessionState, string> = {
  idle: "🎤",
  connecting: "…",
  ready: "🎤",
  talking: "⏹",
  thinking: "…",
  speaking: "🔊",
  error: "🎤",
  "no-mic": "🎤",
};

// The shared /healthz poll (serviceAvailability.ts): agui-voice is off in the lite profiles.
const VOICE_SERVICE: readonly ServiceKey[] = ["agui-voice"];
const VOICE_OFF_LABEL = `Voice is off: the voice service (agui-voice) is not running. To start it: ${SERVICES["agui-voice"].startHint}. Then press to check again`;

/**
 * Chrome (and other browsers) only allow an AudioContext to auto-resume when the
 * resume() call happens close enough to a real user gesture — cross a few `await`s
 * (as `connect()` in useVoiceSession.ts does: tearing down any prior session, opening
 * the WebSocket, requesting the mic) and the browser can decide the gesture no
 * longer counts, leaving an AudioContext stuck "suspended". Creating and resuming a
 * throwaway context *synchronously*, inside the click handler itself, "unlocks"
 * audio for the page so the capture/playback contexts created moments later
 * (asynchronously) resume immediately instead of risking staying suspended.
 */
function unlockAudioPlayback(): void {
  try {
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    const ctx = new Ctor();
    if (ctx.state === "suspended") void ctx.resume();
    const buffer = ctx.createBuffer(1, 1, 22050);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    source.start(0);
  } catch {
    // Best-effort — if this fails, the SDK's own resume() still gets its normal chance.
  }
}

/** The human-readable label for whichever provider `group.active` names, e.g.
 * "Whisper (self-hosted)" instead of the bare id "whisper".
 */
function providerLabel(group: VoiceProviderGroup): string {
  return group.options.find((o) => o.id === group.active)?.label ?? group.active;
}

/** Where a space bar press belongs to the page, not to voice: a field being typed in, or another control. */
function spaceBelongsElsewhere(target: EventTarget | null, mic: HTMLElement | null): boolean {
  if (!(target instanceof HTMLElement) || target === mic) return false;
  if (target.isContentEditable) return true;
  return Boolean(target.closest("input, textarea, select, button, a[href], [role='button'], [role='textbox']"));
}

/**
 * Push-to-talk mic for ChatPanel's input row (docs/adr/0033): press to start
 * talking, press again to send — or, once voice is on, hold the space bar and let
 * go to send. Nothing ends a turn on silence. The button shows each state (ready,
 * talking with pulsing rings, waiting, answering); only talking animates, so a
 * connected but idle session leaves the page still. A small ✕ ends voice mode (it
 * also hangs up by itself after a few idle minutes). A voice turn renders in the
 * same transcript as a typed one, and acts on the screen as one does (`turn`), so no
 * separate UI is needed for it here.
 *
 * Also fetches and shows which STT/TTS provider is actually in effect (the admin
 * Settings page's live choice, resolved server-side — see api/providers.py) as a
 * small label next to the button, so which engine answered a voice turn is never
 * a mystery the way it was during this feature's own development.
 */
export function MicButton({
  agent,
  voiceUrl,
  scenarioSlug,
  accessToken,
  disabled = false,
  turn,
}: {
  agent: HttpAgent;
  voiceUrl: string;
  scenarioSlug: string;
  accessToken: string | null;
  /** Until the chat's conversation exists: a voice turn needs one to land in. */
  disabled?: boolean;
  /** The chat's tools, screen and transcript for each spoken turn, and who runs its screen actions. */
  turn?: VoiceTurnBridge;
}) {
  const { state, press, release, end } = useVoiceSession(agent, voiceUrl, scenarioSlug, accessToken, turn);
  const connected = isVoiceConnected(state);
  const active = connected || state === "connecting";
  const voiceService = useServiceAvailability(VOICE_SERVICE).get("agui-voice");
  // Known to be down: say so, rather than ask for the microphone and fail to connect.
  const voiceOff = voiceService === "down" && !active;
  const [providers, setProviders] = useState<VoiceProviders | null>(null);
  const micRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (voiceService !== "up") return; // nothing to ask a stopped service (and no CORS error in the console)
    voiceProviders(voiceUrl, scenarioSlug, accessToken)
      .then(setProviders)
      .catch(() => {}); // best-effort label — a fetch failure just leaves it unshown
  }, [voiceUrl, scenarioSlug, accessToken, voiceService]);

  // A failed connection: check at once whether the service is down, so the button says why.
  useEffect(() => {
    if (state === "error") refreshServiceAvailability();
  }, [state]);

  // Hold the space bar to talk, only while this chat's voice session is on (one at a time app-wide), and never
  // while typing or on another control. Letting go — or leaving the window mid-hold — sends the turn.
  useEffect(() => {
    if (!connected) return;
    let holding = false;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code !== "Space" || event.altKey || event.ctrlKey || event.metaKey) return;
      if (spaceBelongsElsewhere(event.target, micRef.current)) return;
      event.preventDefault(); // no page scroll, and no "click" on the focused mic button
      if (event.repeat || holding) return;
      holding = true;
      press();
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code !== "Space" || !holding) return;
      event.preventDefault();
      holding = false;
      release();
    };
    const onBlur = () => {
      if (!holding) return;
      holding = false;
      release();
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
  }, [connected, press, release]);

  return (
    <span className="chat-mic-wrap">
      <button
        ref={micRef}
        type="button"
        className={`chat-mic chat-mic--${voiceOff ? "off" : state}`}
        onClick={() => {
          if (voiceOff) {
            refreshServiceAvailability(); // started meanwhile? the next press connects
            return;
          }
          if (!active) unlockAudioPlayback(); // must run synchronously in this click handler — see comment above
          press();
        }}
        title={voiceOff ? VOICE_OFF_LABEL : LABELS[state]}
        aria-label={voiceOff ? VOICE_OFF_LABEL : LABELS[state]}
        aria-pressed={state === "talking"}
        disabled={disabled && !active}
      >
        <span className="chat-mic-icon">{ICONS[state]}</span>
        {state === "talking" && (
          <span className="chat-mic-rings" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        )}
      </button>
      {/* Always laid out (hidden while off), so turning voice on never shifts the input row. */}
      <button
        type="button"
        className={`chat-mic-end${active ? "" : " chat-mic-end--hidden"}`}
        onClick={end}
        title="End voice mode"
        aria-label="End voice mode"
        aria-hidden={!active}
        tabIndex={active ? 0 : -1}
      >
        ✕
      </button>
      {providers && (
        <span
          className={`chat-voice-providers${active ? "" : " chat-voice-providers--hidden"}`}
          title="Voice mode's current speech-to-text/text-to-speech engine"
          aria-hidden={!active}
        >
          🎙️ {providerLabel(providers.stt)} · 🔊 {providerLabel(providers.tts)}
        </span>
      )}
    </span>
  );
}
