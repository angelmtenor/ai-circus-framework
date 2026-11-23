# 0033 — Voice mode is push to talk: the user ends each turn, not a voice-activity detector

Source: docs/adr/0033-push-to-talk-voice.md (this repository)

- **Quiet when idle.** Only listening animates (pulsing rings); waiting no longer spins, and a connected but idle
  session draws nothing.
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
- **Every turn leaves a line of numbers, never words.** agui-voice used to log nothing per turn, so "nothing was
  transcribed" could be a silent microphone or a Whisper that dropped the speech. Now each closed turn logs the audio
  that came (`voice turn:` seconds, frames, sample rate, peak and RMS in dBFS, or "no audio from the browser"), and the
  transcription logs its own (`whisper turn:` / `groq stt turn:` language, no-speech probabilities, characters, time).
- **A turn of silence is said, not answered.** A muted or wrong microphone sends zeros: such a turn (no sample above
  -60 dBFS, or no audio at all) is told to the client (`server-message` `{"type": "push-to-talk", "talking": false,
