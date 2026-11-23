import { SilentMicrophoneNotice } from "./MicrophonePicker";
import { unlockAudioPlayback } from "./pcmCapture";
import { refreshServiceAvailability, useServiceAvailability } from "./serviceAvailability";
import { isDictating, type DictationState } from "./useDictation";
import { NO_MIC_LABEL, VOICE_OFF_LABEL, VOICE_SERVICE } from "./voiceLabels";

const LABELS: Record<DictationState, string> = {
  idle: "Dictate: press to listen, press again to write it in the box",
  starting: "Opening the microphone… press to cancel",
  listening: "Listening — press (or Enter) to stop and write it, Esc to discard",
  transcribing: "Writing what you said… press to discard",
  silent: "No sound came from the microphone — press to try again",
  "no-speech": "No words heard — press to try again",
  "no-mic": NO_MIC_LABEL,
  error: "What you said could not be written — press to try again",
};

/**
 * The chat's dictation mic (docs/adr/0033): press to listen, press again and what you said is text in the input, to
 * read, edit and send. Only listening animates; the conversation by voice is MicButton's.
 */
export function DictateButton({
  state,
  micName,
  press,
  disabled = false,
}: {
  state: DictationState;
  micName: string;
  press: () => void;
  disabled?: boolean;
}) {
  const active = isDictating(state);
  const off = useServiceAvailability(VOICE_SERVICE).get("agui-voice") === "down" && !active;
  const label = off ? VOICE_OFF_LABEL : LABELS[state];
  return (
    <>
      <button
        type="button"
        className={`chat-mic chat-mic--${off ? "off" : state === "listening" ? "talking" : state}`}
        onClick={() => {
          if (off) {
            refreshServiceAvailability(); // started meanwhile? the next press listens
            return;
          }
          if (!active) unlockAudioPlayback(); // synchronously, in the click handler
          press();
        }}
        title={label}
        aria-label={label}
        aria-pressed={state === "listening"}
        disabled={disabled && !active}
      >
        <span className="chat-mic-icon">{state === "listening" ? "⏹" : state === "starting" || state === "transcribing" ? "…" : "🎤"}</span>
        {state === "listening" && (
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
    </>
  );
}
