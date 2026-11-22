# 0033 — Voice mode is push to talk: the user ends each turn, not a voice-activity detector

Source: docs/adr/0033-push-to-talk-voice.md (this repository)

Builds on the live voice mode (`agui-voice`, a Pipecat pipeline per WebSocket connection) and changes only how a turn
starts and ends.

**Context.** Voice mode streamed the microphone continuously and let Silero's voice-activity detector decide when the
user had finished: a pause to think, a breath in a long question or a noisy room ended the turn early, sent half a
question to the agent, and made the bot answer over the user. In a live demo that is the failure people remember. The
plan asked for the interaction of Claude's app: press to start talking, press again (or hold and release the space
bar) to stop, with no automatic end-of-speech detection.
