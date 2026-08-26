import { useEffect, useState } from "react";
import type { HttpAgent } from "@ag-ui/client";
import { voiceProviders, type VoiceProviderGroup, type VoiceProviders } from "./apiClient";
import { useVoiceSession } from "./useVoiceSession";

const LABELS: Record<string, string> = {
  idle: "Start voice conversation",
  connecting: "Connecting…",
  listening: "Listening — click to stop",
  speaking: "Speaking — click to stop, or just start talking to interrupt",
  error: "Voice connection failed — click to retry",
};

const ICONS: Record<string, string> = {
  idle: "🎤",
  connecting: "…",
  listening: "🎤",
  speaking: "⏹",
  error: "🎤",
};

/**
 * Chrome (and other browsers) only allow an AudioContext to auto-resume when the
 * resume() call happens close enough to a real user gesture — cross a few `await`s
 * (as `start()` in useVoiceSession.ts does: tearing down any prior session, opening
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

/**
 * Live mic toggle for ChatPanel's input row — starts/stops the WebSocket voice
 * session from useVoiceSession.ts. Once active (listening/speaking), this same
 * button is the stop control: a visibly different, pulsing/animated state makes it
 * unambiguous that (a) the mic is live and (b) clicking it again ends the call —
 * the CSS animations live in App.css's `.chat-mic--*` rules. A voice turn renders
 * in the same transcript as a typed one, so no separate UI is needed for it here.
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
}: {
  agent: HttpAgent;
  voiceUrl: string;
  scenarioSlug: string;
  accessToken: string | null;
}) {
  const { state, start, stop } = useVoiceSession(agent, voiceUrl, scenarioSlug, accessToken);
  const active = state !== "idle" && state !== "error";
  const [providers, setProviders] = useState<VoiceProviders | null>(null);

  useEffect(() => {
    voiceProviders(voiceUrl, scenarioSlug, accessToken)
      .then(setProviders)
      .catch(() => {}); // best-effort label — a fetch failure just leaves it unshown
  }, [voiceUrl, scenarioSlug, accessToken]);

  return (
    <span className="chat-mic-wrap">
      <button
        type="button"
        className={`chat-mic chat-mic--${state}`}
        onClick={() => {
          if (!active) unlockAudioPlayback(); // must run synchronously in this click handler — see comment above
          return active ? stop() : start();
        }}
        title={LABELS[state]}
        aria-label={LABELS[state]}
        aria-pressed={active}
      >
        <span className="chat-mic-icon">{ICONS[state]}</span>
        {(state === "listening" || state === "speaking") && (
          <span className="chat-mic-rings" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        )}
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
