# 0033 — Voice mode is push to talk: the user ends each turn, not a voice-activity detector

Source: docs/adr/0033-push-to-talk-voice.md (this repository)

  "reason": "silent"}`), which stops waiting and says above the input row that no sound came, naming the microphone in
  use.
- **What holds no words is never transcribed.** Whisper always writes something: a click came out as "Thank you." and
  a near-silent second as a Russian subtitle credit, each sent to the agent as the user's question. Level cannot tell
  them apart (the click peaked at -9 dBFS, clear quiet speech at -36), so every recording, of either button and for
  hosted and local Whisper alike, first goes through the Silero model faster-whisper bundles (about 5 ms,
  `core/speech.py`): with no speech in it the client is told (`"reason": "no-speech"`), and otherwise only the part
  from the first speech to the last is transcribed. It never starts or ends a turn.
- **The microphone can be chosen.** The browser's choice is not always the system's default (a VR headset's virtual
  input was picked over the laptop's microphone), so the notice and Settings → Microphone list the audio inputs
  (`microphone.ts`). The choice is each viewer's, kept in the browser; one that is unplugged falls back to the browser's
  choice, and changing it ends the open session. The Settings test measures the level in the browser only.
- **Local Whisper's language is used too.** Pipecat's transcript carried none, so a Spanish question was answered in the
  English voice; the one Whisper detects now goes with it.
- **Local Whisper runs int8 and keeps quiet speech.** On a CPU "default" fell back to float32; int8 takes about half
  the memory and time. Pipecat dropped every segment at a no-speech probability of 0.4 or more, which lost a clear
  English question at -36 dBFS (0.41); the threshold is now 0.6, faster-whisper's own.
- **A hosted Whisper is an option, off by default.** STT `groq` (Settings → Voice, or `VOICE_STT_PROVIDER=groq`; needs
  `GROQ_API_KEY`) transcribes with Groq's `whisper-large-v3-turbo`, which takes 10-16 s a sentence on this project's
  CPUs. No language is sent, so Groq detects it and the transcript carries it to the Spanish or English voice. A turn
  it cannot transcribe (no network, a bad key, the free tier's limits, no answer within 6 s) goes to the pre-warmed
  local Whisper. **The audio of each turn leaves the cluster** for that vendor, which is why the default stays local.

**Consequences.**
- A turn is exactly what the user held: pauses no longer cut questions, and the bot never starts answering while the
  user is still talking. The cost is one more action per turn (press again, or let go of the space bar).
- A dictated message is a typed one: answered in writing and at full length, and read aloud only by its speaker icon.
- Whisper transcribes the whole press at once, after the release, so a long turn is transcribed in one piece. A
  streaming STT (Deepgram, optional) still finalizes on its own pauses within a press, and each final starts a reply;
  push to talk is designed for the default Whisper path.
- The wire protocol gains two message types in each direction; an older browser build that streams continuously and
  never presses gets no turns at all, so ui-react and agui-voice must be deployed together. A browser that sends no
  `voice-turn` gets the old behaviour: a spoken answer with no screen actions.
- A spoken turn with a screen action costs one more model call than a typed one, which stops after the call: the ear
  needs the sentence the eye does not.
- The agui-voice image no longer carries the `silero` extra (its ONNX runtime stays, for Piper).
