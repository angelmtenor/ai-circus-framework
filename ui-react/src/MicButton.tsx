import { useEffect, useRef, useState, type ReactNode } from "react";
import type { HttpAgent } from "@ag-ui/client";
import { voiceProviders, type VoiceProviderGroup, type VoiceProviders } from "./apiClient";
import { SilentMicrophoneNotice } from "./MicrophonePicker";
import { unlockAudioPlayback } from "./pcmCapture";
import { refreshServiceAvailability, useServiceAvailability } from "./serviceAvailability";
import { isVoiceConnected, useVoiceSession, type VoiceSessionState, type VoiceTurnBridge } from "./useVoiceSession";
import { NO_MIC_LABEL, VOICE_OFF_LABEL, VOICE_SERVICE } from "./voiceLabels";

const LABELS: Record<VoiceSessionState, string> = {
  idle: "Talk with the assistant: press to start talking, press again to send. It answers aloud",
  connecting: "Connecting… talk when the button turns red (press to cancel)",
  ready: "Press to talk, or hold the space bar",
  talking: "Listening — press again, or let go of the space bar, to send",
  thinking: "Waiting for the answer — press to stop it",
  speaking: "Answering — press to stop it, or hold the space bar to interrupt and talk",
  silent: "No sound came from the microphone — press to try again",
  "no-speech": "No words heard — press to try again",
  error: "Voice connection failed — press to retry",
  "no-mic": NO_MIC_LABEL,
};

/** The glyph of a conversation by voice, as against dictation's microphone. */
function VoiceBars() {
  return (
    <svg className="chat-voice-bars" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      <path d="M2 6.5v3M5 4v8M8 2v12M11 4v8M14 6.5v3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" fill="none" />
    </svg>
  );
}

function icon(state: VoiceSessionState): ReactNode {
  if (state === "connecting") return "…";
  if (state === "talking") return "↑"; // a press sends
  if (state === "thinking" || state === "speaking") return "⏹"; // a press stops
  return <VoiceBars />;
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
 * The conversation by voice in ChatPanel's input row (docs/adr/0033), beside the dictation mic (`dictation`), whose
 * place its ✕ takes while a session is on. Press to start talking, press again to send — or, once voice is on, hold
 * the space bar and let go to send. Nothing ends a turn on silence, and a press while the answer is being prepared or
 * said only stops it: the next press talks. Only talking animates, so a connected but idle session leaves the page
 * still (it hangs up by itself after a few idle minutes). A voice turn renders in the same transcript as a typed one,
 * and acts on the screen as one does (`turn`), so no separate UI is needed for it here.
 *
 * The tooltip says which STT/TTS provider is actually in effect (the admin Settings page's live choice, resolved
 * server-side — see api/providers.py), so which engine answered a voice turn is never a mystery.
 */
export function MicButton({
  agent,
  voiceUrl,
  scenarioSlug,
  accessToken,
  disabled = false,
  turn,
  onState,
  dictation,
}: {
  agent: HttpAgent;
  voiceUrl: string;
  scenarioSlug: string;
  accessToken: string | null;
  /** Until the chat's conversation exists: a voice turn needs one to land in. */
  disabled?: boolean;
  /** The chat's tools, screen and transcript for each spoken turn, and who runs its screen actions. */
  turn?: VoiceTurnBridge;
  /** Told each change, so the chat can say what the conversation is doing. */
  onState?: (state: VoiceSessionState) => void;
  /** The dictation mic, shown while no conversation by voice is on. */
  dictation?: ReactNode;
}) {
  const { state, micName, press, talk, release, end } = useVoiceSession(agent, voiceUrl, scenarioSlug, accessToken, turn);
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

  const onStateRef = useRef(onState);
  useEffect(() => {
    onStateRef.current = onState;
  });
  useEffect(() => onStateRef.current?.(state), [state]);

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
      talk();
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
  }, [connected, talk, release]);

  const label = voiceOff ? VOICE_OFF_LABEL : LABELS[state];
  const engines = providers ? ` (speech to text ${providerLabel(providers.stt)}, text to speech ${providerLabel(providers.tts)})` : "";
  return (
    <span className="chat-mic-wrap">
      {active ? (
        <button type="button" className="chat-mic chat-mic-end" onClick={end} title="End voice mode" aria-label="End voice mode">
          ✕
        </button>
      ) : (
        dictation
      )}
      <button
        ref={micRef}
        type="button"
        className={`chat-mic chat-mic--${voiceOff ? "off" : state}`}
        onClick={() => {
          if (voiceOff) {
            refreshServiceAvailability(); // started meanwhile? the next press connects
            return;
          }
          if (!active) unlockAudioPlayback(); // must run synchronously in this click handler
          press();
        }}
        title={label + (voiceOff ? "" : engines)}
        aria-label={label}
        aria-pressed={state === "talking"}
        disabled={disabled && !active}
      >
        <span className="chat-mic-icon">{icon(state)}</span>
        {state === "talking" && (
          <span className="chat-mic-rings" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        )}
      </button>
      {state === "silent" && <SilentMicrophoneNotice micName={micName} />}
      {(state === "no-speech" || state === "no-mic" || state === "error") && (
        <span className="chat-mic-notice" role="status">
          {LABELS[state]}.
        </span>
      )}
    </span>
  );
}
