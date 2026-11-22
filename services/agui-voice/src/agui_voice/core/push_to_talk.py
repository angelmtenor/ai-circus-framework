"""
- Title:    Push-to-talk turn control for the live voice pipeline
- Author:   Angel Martinez-Tenor

The user, not a voice-activity detector, decides when a voice turn starts and ends (docs/adr/0033): the browser
sends an RTVI `client-message` of type `push-to-talk` with `{"talking": true}` when the user presses the mic (or
holds the space bar) and `{"talking": false}` when they press it again (or let go). `PushToTalkProcessor` sits right
after the transport input, where Silero's `VADProcessor` used to be, and turns those messages into the frames the
VAD used to emit: `VADUserStartedSpeakingFrame`/`VADUserStoppedSpeakingFrame` bound the segment a segmented STT
(Whisper) transcribes, `UserStartedSpeakingFrame`/`UserStoppedSpeakingFrame` mark the user's turn for RTVI clients,
and a press first interrupts whatever the bot is saying or still preparing (`AgentBridgeProcessor` cancels its
in-flight call on that `InterruptionFrame`). Nothing ends a turn on silence.

A turn is bounded: past `max_turn_secs` of audio the processor ends it itself and tells the client with a
`server-message` `{"type": "push-to-talk", "talking": false, "reason": "limit"}`, so a stuck key or a client that never
sends the stop cannot grow the STT buffer without limit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame, RTVIServerMessageFrame

PUSH_TO_TALK_MESSAGE = "push-to-talk"
"""The RTVI `client-message` type (`t`) the browser sends; ui-react's useVoiceSession.ts uses the same string."""

DEFAULT_MAX_TURN_SECS = 60.0
"""The longest single press, in seconds of received audio, before the turn is ended for the user."""


def requested_talking(frame: RTVIClientMessageFrame) -> bool | None:
    """What a push-to-talk message asks for: True (start talking), False (stop), None (not a push-to-talk message).

    Only a literal `true` starts a turn; anything else in a push-to-talk message (false, missing, malformed) stops it,
    so a garbled message can never leave the mic open.
    """
    if frame.type != PUSH_TO_TALK_MESSAGE:
        return None
    return isinstance(frame.data, Mapping) and frame.data.get("talking") is True


class PushToTalkProcessor(FrameProcessor):
    """Starts and ends the user's voice turn on the client's push-to-talk messages instead of on detected speech."""

    def __init__(self, *, max_turn_secs: float = DEFAULT_MAX_TURN_SECS, **kwargs: Any) -> None:
        """`max_turn_secs` bounds one turn (seconds of audio received while talking)."""
        super().__init__(**kwargs)
        self._max_turn_secs = max_turn_secs
        self._talking = False
        self._turn_secs = 0.0

    @property
    def talking(self) -> bool:
        """Whether the user's turn is open (the mic is pressed)."""
        return self._talking

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward every frame, then open/close the turn on push-to-talk messages and bound an open turn's length."""
        await super().process_frame(frame, direction)
        # Forward first (as VADProcessor did): audio reaches the STT buffer before the turn that includes it closes.
        await self.push_frame(frame, direction)

        if isinstance(frame, RTVIClientMessageFrame):
            talking = requested_talking(frame)
            if talking is True:
                await self._start_turn()
            elif talking is False:
                await self._end_turn()
        elif isinstance(frame, InputAudioRawFrame) and self._talking:
            bytes_per_second = frame.sample_rate * frame.num_channels * 2
            if bytes_per_second > 0:
                self._turn_secs += len(frame.audio) / bytes_per_second
            if self._turn_secs >= self._max_turn_secs:
                await self._end_turn()
                await self.push_frame(
                    RTVIServerMessageFrame(data={"type": PUSH_TO_TALK_MESSAGE, "talking": False, "reason": "limit"})
                )

    async def _start_turn(self) -> None:
        if self._talking:
            return
        self._talking = True
        self._turn_secs = 0.0
        # Pressing to talk is the barge-in: stop the bot's audio and any reply still being prepared, then open the
        # segment. The interruption goes first so nothing it resets can drop the start of the new segment.
        await self.broadcast_interruption()
        await self.broadcast_frame(VADUserStartedSpeakingFrame)
        await self.broadcast_frame(UserStartedSpeakingFrame)

    async def _end_turn(self) -> None:
        if not self._talking:
            return
        self._talking = False
        await self.broadcast_frame(VADUserStoppedSpeakingFrame)
        await self.broadcast_frame(UserStoppedSpeakingFrame)
