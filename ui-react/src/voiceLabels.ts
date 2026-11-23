// What the dictation mic (DictateButton.tsx) and the live voice button (MicButton.tsx) say alike.
import { SERVICES, type ServiceKey } from "./serviceAvailability";

/** The shared /healthz poll (serviceAvailability.ts): agui-voice is off in the lite profiles. */
export const VOICE_SERVICE: readonly ServiceKey[] = ["agui-voice"];
export const VOICE_OFF_LABEL = `Voice is off: the voice service (agui-voice) is not running. To start it: ${SERVICES["agui-voice"].startHint}. Then press to check again`;
export const NO_MIC_LABEL =
  "No microphone: the browser blocked it or found none. Allow the microphone for this site (the icon at the left of the address bar), then press to retry";
