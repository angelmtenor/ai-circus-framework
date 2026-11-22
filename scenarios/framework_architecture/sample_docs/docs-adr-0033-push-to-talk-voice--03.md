# 0033 — Voice mode is push to talk: the user ends each turn, not a voice-activity detector

Source: docs/adr/0033-push-to-talk-voice.md (this repository)

**Consequences.**
- A turn is exactly what the user held: pauses no longer cut questions, and the bot never starts answering while the
  user is still talking. The cost is one more action per turn (press again, or let go of the space bar).
- Whisper transcribes the whole press at once, after the release, so a long turn is transcribed in one piece. A
  streaming STT (Deepgram, optional) still finalizes on its own pauses within a press, and each final starts a reply;
  push to talk is designed for the default Whisper path.
- The wire protocol gains two message types in each direction; an older browser build that streams continuously and
  never presses gets no turns at all, so ui-react and agui-voice must be deployed together. A browser that sends no
  `voice-turn` gets the old behaviour: a spoken answer with no screen actions.
- A spoken turn with a screen action costs one more model call than a typed one, which stops after the call: the ear
  needs the sentence the eye does not.
- The agui-voice image no longer carries the `silero` extra (its ONNX runtime stays, for Piper).
