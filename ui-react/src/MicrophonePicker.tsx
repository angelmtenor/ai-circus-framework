import { useState } from "react";
import { chooseMicrophone, testMicrophone, useMicrophoneChoice, useMicrophones } from "./microphone";

const AUTOMATIC = "";

/** Which microphone voice mode listens to: the browser's own choice, or one of its audio inputs. */
export function MicrophoneSelect({ id, microphones }: { id?: string; microphones: ReturnType<typeof useMicrophones>["microphones"] }) {
  const choice = useMicrophoneChoice();
  // The browser may have renewed its ids: the same name is the same microphone (openMicrophone finds it so too).
  const current = choice && (microphones.find((m) => m.deviceId === choice.deviceId) ?? microphones.find((m) => m.label === choice.label));
  return (
    <select
      id={id}
      className="settings-model-select"
      aria-label="Microphone"
      value={current?.deviceId ?? choice?.deviceId ?? AUTOMATIC}
      onChange={(e) => chooseMicrophone(microphones.find((m) => m.deviceId === e.target.value) ?? null)}
    >
      <option value={AUTOMATIC}>The browser&apos;s choice</option>
      {choice && !current && (
        <option value={choice.deviceId}>
          {choice.label}
          {microphones.length > 0 ? " — not connected: the browser's choice is used" : ""}
        </option>
      )}
      {microphones.map((m) => (
        <option key={m.deviceId} value={m.deviceId}>
          {m.label}
        </option>
      ))}
    </select>
  );
}

/** The mic button's "no sound" notice, with the choice right there. */
export function SilentMicrophoneNotice({ micName }: { micName: string }) {
  const { microphones } = useMicrophones();
  return (
    <span className="chat-mic-notice">
      <span role="status">
        I can&apos;t hear you: no sound came from {micName ? <strong>{micName}</strong> : "the microphone"}. Check that it is not muted,
        or choose another one, then press to try again.
      </span>
      {microphones.length > 1 && <MicrophoneSelect microphones={microphones} />}
    </span>
  );
}

/** Settings → Microphone: every user's own choice, kept in this browser, and a test that stays in the browser too. */
export function MicrophoneSection() {
  const { microphones, refresh } = useMicrophones();
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  async function test() {
    setTesting(true);
    setResult(null);
    try {
      const { label, heard } = await testMicrophone();
      const name = label || "the microphone";
      setResult(
        heard
          ? { ok: true, text: `Sound came from ${name}.` }
          : { ok: false, text: `No sound came from ${name}: check that it is not muted, or choose another one.` },
      );
      refresh(); // the browser names the devices once the microphone is allowed
    } catch (e) {
      const refused = (e as { name?: string } | null)?.name === "NotAllowedError";
      setResult({
        ok: false,
        text: refused
          ? "The browser blocked the microphone: allow it for this site (the icon at the left of the address bar)."
          : `No microphone could be opened: ${(e as Error).message}`,
      });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="panel-card settings-card">
      <h3>Microphone</h3>
      <p className="panel-hint">
        Which microphone voice mode listens to. The browser&apos;s choice is not always the system&apos;s default one. Saved to this
        browser.
      </p>
      <MicrophoneSelect microphones={microphones} />
      {microphones.length === 0 && (
        <p className="panel-hint">The browser names the microphones once you allow one: press Test.</p>
      )}
      <div className="settings-microphone-test">
        <button className="btn-secondary" onClick={test} disabled={testing}>
          {testing ? "Listening, say something…" : "▶ Test"}
        </button>
      </div>
      {result && (
        <div className={`settings-result ${result.ok ? "settings-result--ok" : "settings-result--fail"}`} role="status">
          {result.ok ? "✅" : "❌"} {result.text}
        </div>
      )}
    </div>
  );
}
