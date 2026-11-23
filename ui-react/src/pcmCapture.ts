// Microphone capture as the 16 kHz mono PCM agui-voice takes, for dictation (useDictation.ts) and the live voice mode
// (useVoiceSession.ts), and which of the two has the microphone: one at a time in the whole app.

/**
 * Must match the server's fixed STT rate (Whisper expects 16 kHz): it trusts whatever rate it is told, so sending the
 * mic's native rate (44.1/48 kHz) breaks STT without any error. Requesting this rate on the AudioContext makes the
 * browser resample the mic stream itself.
 */
export const CAPTURE_SAMPLE_RATE = 16_000;
const CAPTURE_BUFFER_SIZE = 4096;

/** Capture keeps going this long after the user stops: its buffer (256 ms) still holds the last word. */
export const RELEASE_TAIL_MS = 320;

/** Calls `onChunk` with each buffer of the microphone's audio until the returned context is closed. */
export function capturePcm(mediaStream: MediaStream, onChunk: (samples: Int16Array<ArrayBuffer>) => void): AudioContext {
  const audioContext = new AudioContext({ sampleRate: CAPTURE_SAMPLE_RATE });
  // Fire-and-forget: unlockAudioPlayback() in the click handler is what satisfies the browser's autoplay rule; this is
  // a safety net, never awaited, so a slow resume cannot block a connection.
  audioContext.resume().catch(() => {});
  const sourceNode = audioContext.createMediaStreamSource(mediaStream);
  // ScriptProcessorNode is deprecated in favor of AudioWorklet, but it needs no async module loading (no blob-URL step
  // to hang on) and its synchronous callback is enough here — a deliberate tradeoff after the AudioWorklet-based SDK
  // path proved unreliable in this environment (useVoiceSession.ts).
  const processor = audioContext.createScriptProcessor(CAPTURE_BUFFER_SIZE, 1, 1);
  processor.onaudioprocess = (event) => {
    const float32 = event.inputBuffer.getChannelData(0);
    const int16 = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      const clamped = Math.max(-1, Math.min(1, float32[i]));
      int16[i] = clamped < 0 ? clamped * 32768 : clamped * 32767;
    }
    onChunk(int16);
  };
  sourceNode.connect(processor);
  // It only fires while connected to a destination: a zero-gain node, or the user would hear their own microphone.
  const silentSink = audioContext.createGain();
  silentSink.gain.value = 0;
  processor.connect(silentSink);
  silentSink.connect(audioContext.destination);
  return audioContext;
}

/** The captured chunks as one WAV file. */
export function pcmToWav(chunks: readonly Int16Array<ArrayBuffer>[]): Blob {
  const bytes = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const header = new DataView(new ArrayBuffer(44));
  const text = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) header.setUint8(offset + i, value.charCodeAt(i));
  };
  text(0, "RIFF");
  header.setUint32(4, 36 + bytes, true);
  text(8, "WAVEfmt ");
  header.setUint32(16, 16, true);
  header.setUint16(20, 1, true); // PCM
  header.setUint16(22, 1, true); // mono
  header.setUint32(24, CAPTURE_SAMPLE_RATE, true);
  header.setUint32(28, CAPTURE_SAMPLE_RATE * 2, true);
  header.setUint16(32, 2, true);
  header.setUint16(34, 16, true);
  text(36, "data");
  header.setUint32(40, bytes, true);
  return new Blob([header, ...chunks], { type: "audio/wav" });
}

/**
 * Browsers only let an AudioContext run when it is resumed close enough to a real user gesture: cross a few `await`s
 * (the mic prompt, a socket) and it can stay "suspended". Resuming a throwaway context synchronously, inside the click
 * handler itself, unlocks audio for the page, so the contexts created moments later start at once.
 */
export function unlockAudioPlayback(): void {
  try {
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    const ctx = new Ctor();
    if (ctx.state === "suspended") void ctx.resume();
    const buffer = ctx.createBuffer(1, 1, 22050);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    source.onended = () => void ctx.close().catch(() => {});
    source.start(0);
  } catch {
    // Best-effort — if this fails, the context's own resume() still gets its chance.
  }
}

let releaseMicrophone: (() => void) | null = null;

/** Takes the microphone, ending whatever had it. `release` is called when something else takes it. */
export function claimMicrophone(release: () => void): void {
  const previous = releaseMicrophone;
  releaseMicrophone = release;
  if (previous && previous !== release) previous();
}

/** Gives the microphone back, if `release` still has it. */
export function dropMicrophone(release: () => void): void {
  if (releaseMicrophone === release) releaseMicrophone = null;
}
