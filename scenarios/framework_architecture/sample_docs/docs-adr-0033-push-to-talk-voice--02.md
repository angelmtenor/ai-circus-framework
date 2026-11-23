# 0033 — Voice mode is push to talk: the user ends each turn, not a voice-activity detector

Source: docs/adr/0033-push-to-talk-voice.md (this repository)

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
