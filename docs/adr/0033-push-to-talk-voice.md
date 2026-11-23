# 0033 — Voice mode is push to talk: the user ends each turn, not a voice-activity detector

Builds on the live voice mode (`agui-voice`, a Pipecat pipeline per WebSocket connection) and changes only how a turn
starts and ends.

**Context.** Voice mode streamed the microphone continuously and let Silero's voice-activity detector decide when the
user had finished: a pause to think, a breath in a long question or a noisy room ended the turn early, sent half a
question to the agent, and made the bot answer over the user. In a live demo that is the failure people remember. The
plan asked for the interaction of Claude's app: press to start talking, press again (or hold and release the space
bar) to stop, with no automatic end-of-speech detection.

**Decision.**
- **Two buttons, as in Claude's app: dictate, and talk.** The microphone **dictates**: a press listens, the next one (or
  Enter) stops, and what was said becomes text in the chat's input, which the user reads, edits and sends with Enter as
  any message; Esc discards it. It is a recording posted once to agui-voice's `POST /stt/{scenario_slug}` (entitlement
  checked, at most 120 s, the STT provider picked in Settings), with no pipeline and no agent (`useDictation.ts`). The
  voice button beside it is the **conversation** the rest of this record describes. One thing has the microphone at a
  time (`pcmCapture.ts`): starting one ends the other. A single button for both made people wait for a turn to
  "finish" that in fact sent whatever the microphone had heard.
- **The client bounds the turn.** Each press and release is an RTVI `client-message` of type `push-to-talk`
  (`{"talking": true|false}`) on the same WebSocket. In the pipeline, `PushToTalkProcessor`
  (`agui_voice/core/push_to_talk.py`) takes the VAD's place right after the transport input and turns those
  messages into the frames the VAD used to emit: `VADUserStartedSpeakingFrame`/`VADUserStoppedSpeakingFrame` bound
  the segment Whisper (a segmented STT) transcribes, and `UserStartedSpeakingFrame`/`UserStoppedSpeakingFrame` mark
  the user's turn for RTVI clients. Nothing ends a turn on silence; Silero is no longer loaded, warmed or installed.
- **A press while waiting or answering stops, and only that.** It used to be the barge-in, so pressing to stop a slow
  answer opened another turn, recorded a second of nothing and cancelled the answer. Now it sends `{"talking": false,
  "cancel": true}`: the answer ends, a transcript still on its way is dropped (transcription runs off the pipeline, so
  an interruption does not cancel it) and the next press talks. The **space bar** held then is the barge-in: the turn's
  opening broadcasts an `InterruptionFrame` first (the bot's audio stops, and `AgentBridgeProcessor` cancels a reply
  still being prepared), and the browser drops the queued reply audio at once. A second transcript replaces a run in
  progress: two at once would be said interleaved.
- **The browser sends audio only while talking** (`useVoiceSession.ts`), plus a 320 ms tail after the release so the
  last word, still in the capture buffer, is not cut and the stop does not overtake it. Between turns nothing is
  heard by the server.
- **The space bar is a hold-to-talk key once voice is on** for a chat (`MicButton.tsx`): never while typing in a field
  or with another control focused, and leaving the window mid-hold sends the turn. A first press of the mic button
  connects and starts talking as soon as the pipeline is ready (the button turns red). While agui-voice is not running
  (`serviceAvailability.ts`'s shared `/healthz` poll; the lite profiles keep it at 0) the button says voice is off and
  how to start it, and a press only checks again; a refused microphone has its own message.
- **Everything is bounded.** One turn is at most 60 s of audio: the server ends it itself and tells the client
  (`server-message` `{"type": "push-to-talk", "talking": false, "reason": "limit"}`), so a stuck key or a client that
  never sends the stop cannot grow the STT buffer; the client stops at the same point. A turn the agent never starts
  on (nothing was transcribed) gives the button back after 30 s; once the agent has started (`bot-llm-started`), the
  client waits up to 2 minutes for its answer, since a cold start plus a slow model once took 30 s. A connected session
  nobody uses hangs up after 3 minutes, which frees the server's pipeline and the microphone. A ✕ beside the mic ends voice mode at any time.
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
