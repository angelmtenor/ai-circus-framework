import { useRef, useState } from "react";
import { speakText } from "./apiClient";

/**
 * Loudspeaker icon rendered on each assistant chat bubble — synthesizes and plays
 * that single message via agui-voice's one-shot `POST /tts/{scenario_slug}`
 * (independent of the live mic session in MicButton.tsx/useVoiceSession.ts).
 *
 * Only one clip plays at a time app-wide: `activePlayback` is a module-level
 * singleton, so clicking a different message's speaker icon while one is already
 * playing stops that one first — never two overlapping voices.
 */
let activePlayback: { audio: HTMLAudioElement; stop: () => void } | null = null;

function stopActivePlayback(): void {
  activePlayback?.stop();
  activePlayback = null;
}

export function SpeakerButton({
  text,
  voiceUrl,
  scenarioSlug,
  accessToken,
}: {
  text: string;
  voiceUrl: string;
  scenarioSlug: string;
  accessToken: string | null;
}) {
  const [state, setState] = useState<"idle" | "loading" | "playing" | "error">("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);

  function stopThis(): void {
    audioRef.current?.pause();
    setState("idle");
  }

  async function play() {
    if (state === "loading") return;
    if (state === "playing") {
      stopActivePlayback();
      return;
    }
    stopActivePlayback(); // only one clip plays at a time
    setState("loading");
    try {
      const blob = await speakText(voiceUrl, scenarioSlug, text, accessToken);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => {
        setState("idle");
        URL.revokeObjectURL(url);
        if (activePlayback?.audio === audio) activePlayback = null;
      };
      audio.onerror = () => {
        setState("error");
        URL.revokeObjectURL(url);
        if (activePlayback?.audio === audio) activePlayback = null;
      };
      await audio.play();
      activePlayback = { audio, stop: stopThis };
      setState("playing");
    } catch {
      setState("error");
    }
  }

  return (
    <button
      type="button"
      className={`chat-speaker chat-speaker--${state}`}
      onClick={play}
      title={state === "playing" ? "Stop" : "Speak this reply"}
      aria-label={state === "playing" ? "Stop speaking" : "Speak this reply"}
    >
      {state === "loading" ? "◐" : state === "playing" ? "⏹" : state === "error" ? "⚠️" : "🔊"}
    </button>
  );
}
