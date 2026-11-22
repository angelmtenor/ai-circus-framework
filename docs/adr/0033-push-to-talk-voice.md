# 0033 — Voice mode is push to talk: the user ends each turn, not a voice-activity detector

Builds on the live voice mode (`agui-voice`, a Pipecat pipeline per WebSocket connection) and changes only how a turn
starts and ends.

**Context.** Voice mode streamed the microphone continuously and let Silero's voice-activity detector decide when the
user had finished: a pause to think, a breath in a long question or a noisy room ended the turn early, sent half a
question to the agent, and made the bot answer over the user. In a live demo that is the failure people remember. The
plan asked for the interaction of Claude's app: press to start talking, press again (or hold and release the space
bar) to stop, with no automatic end-of-speech detection.

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
  to four plain sentences, no lists or tables, and the detail shown on screen (or offered). The tools, data and checks
  are those of a typed turn. Without it the first insurance answer (SHAP factors, then offers) took about two minutes
  to say.
- **A spoken turn acts on the screen as a typed one does.** The bridge used to run the agent with no tools and no
  context, so by voice the form was never filled, no chart was drawn and the guide could not open anything, while the
  form agent said it had done so. Now each press sends a `client-message` of type `voice-turn` carrying what a typed
  run of that chat sends: the tools and readable context registered with the chat's CopilotKit, its forwarded props
  (the guide's `guide`, a document scope, the MCP route) and its transcript, text only and the latest 40 messages, so
  a spoken follow-up knows what was typed before. The bridge validates it (AG-UI types; no system or developer
  message; at most 32 tools, 32 context entries, 100 messages and 512 KB, otherwise the turn runs without it), adds
  `spoken`, and runs the agent with it. A call to one of the chat's tools is sent back as a `server-message`
  `{"type": "frontend-tool-call", "id", "name", "arguments"}` as soon as its arguments are known, and the chat carries
  it out as after a typed turn (`onFrontendToolCall`, the call's chip in the transcript). With Gemini, LangGraph's
  AG-UI adapter streams only a call's start and end, so the arguments come from the run's closing `MESSAGES_SNAPSHOT`
  (which the browser's HttpAgent applies for a typed turn); a call with no arguments anywhere is dropped. The chat never sends a result back, so the
  bridge answers the call as the chat does ("Displayed to the user.") and runs the agent once more, so the user also
  hears what was done: at most three runs per turn, said as one response. The agent's own tools still run in the
  service, and nothing in this widens a typed turn's reach: the same browser could send the same input over HTTP, and
  the agent checks the caller and the conversation either way.

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
