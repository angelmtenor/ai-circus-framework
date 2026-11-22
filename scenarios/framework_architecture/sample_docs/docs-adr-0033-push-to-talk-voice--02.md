# 0033 — Voice mode is push to talk: the user ends each turn, not a voice-activity detector

Source: docs/adr/0033-push-to-talk-voice.md (this repository)

**Decision.**
- **The client bounds the turn.** Each press and release is an RTVI `client-message` of type `push-to-talk`
  (`{"talking": true|false}`) on the same WebSocket. In the pipeline, `PushToTalkProcessor`
  (`agui_voice/core/push_to_talk.py`) takes the VAD's place right after the transport input and turns those
  messages into the frames the VAD used to emit: `VADUserStartedSpeakingFrame`/`VADUserStoppedSpeakingFrame` bound
  the segment Whisper (a segmented STT) transcribes, and `UserStartedSpeakingFrame`/`UserStoppedSpeakingFrame` mark
  the user's turn for RTVI clients. Nothing ends a turn on silence; Silero is no longer loaded, warmed or installed.
- **A press is the barge-in.** It broadcasts an `InterruptionFrame` first (the bot's audio stops, and
  `AgentBridgeProcessor` cancels a reply still being prepared), then opens the segment. The browser also drops the
  queued reply audio at once, so the interruption is heard immediately.
- **The browser sends audio only while talking** (`useVoiceSession.ts`), plus a 320 ms tail after the release so the
  last word, still in the capture buffer, is not cut and the stop does not overtake it. Between turns nothing is
  heard by the server.
- **The space bar is a hold-to-talk key once voice is on** for a chat (`MicButton.tsx`): never while typing in a field
  or with another control focused, and leaving the window mid-hold sends the turn. A first press of the mic button
  connects and starts talking as soon as the pipeline is ready (the button turns red).
- **Everything is bounded.** One turn is at most 60 s of audio: the server ends it itself and tells the client
  (`server-message` `{"type": "push-to-talk", "talking": false, "reason": "limit"}`), so a stuck key or a client that
  never sends the stop cannot grow the STT buffer; the client stops at the same point. A turn the agent never starts
  on (nothing was transcribed) gives the button back after 30 s; once the agent has started (`bot-llm-started`), the
  client waits up to 2 minutes for its answer, since a cold start plus a slow model once took 30 s. A connected session
  nobody uses hangs up after 3 minutes, which frees the server's pipeline and the microphone. A ✕ beside the mic ends voice mode at any time.
- **Quiet when idle.** Only the talking state animates (pulsing rings); a connected but idle session draws nothing.
- **A spoken turn lands in the chat's open conversation.** The browser passes its id as `?thread_id=` (checked for
  shape, 4400 otherwise) and the bridge runs the agent on it. The agents serve a run only for a conversation the caller
  owns, so the bridge's old fixed `thread_id` (the scenario slug) had been refused with 404 since conversation history
  arrived; now the spoken turns are also stored with the typed ones. The mic waits until that conversation exists, and
  opening another conversation hangs up. The interruption itself is not sent as a frame (the browser's serializer
  cannot read one): the browser learns of it from RTVI's `bot-interrupted`.
- **A spoken turn is answered for the ear.** The bridge sends each run with `forwardedProps: {"spoken": true}`, and
  assistant, rag-agent and form-agent then end their system prompt with `ai_circus_shared.spoken`'s instructions: two
  to four plain sentences, no lists or tables, and an offer to show the detail on screen. The tools, data and checks
  are those of a typed turn. Without it the first insurance answer (SHAP factors, then offers) took about two minutes
  to say.
