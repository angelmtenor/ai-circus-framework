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

Each closed turn logs one line of numbers (`TurnAudio.describe`): how much audio the browser sent while the mic was
pressed, at what sample rate and how loud. "Nothing was transcribed" then tells a mic that sent nothing or silence from
speech the STT could not read (providers.py logs that half). Never the audio or its words. A turn with nothing but
silence in it is also told to the client (`"reason": "silent"`), which otherwise waits for an answer that will not come;
providers.py says the same of one with sound but no words (`"reason": "no-speech"`).

A `{"talking": false, "cancel": true}` message is the user's stop: whatever is being prepared or said ends, and nothing
is opened. (A press while waiting used to mean "talk again", so pressing to stop started another turn.)
"""

from __future__ import annotations

import math
import sys
from array import array
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from loguru import logger
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


FULL_SCALE = 32768.0
"""16-bit PCM's full scale: 0 dBFS."""

SILENT_PEAK_DBFS = -60.0
"""A turn whose loudest sample is below this has no speech in it: a muted or wrong microphone sends zeros. Quiet speech
is far above it (-36 dBFS was still clear)."""


def dbfs(level: float) -> float:
    """A 16-bit sample level in dBFS (-inf for silence)."""
    return 20 * math.log10(level / FULL_SCALE) if level > 0 else -math.inf


@dataclass
class TurnAudio:
    """What the browser sent during one turn: frames, seconds, sample rate, peak and RMS level (no audio kept)."""

    frames: int = 0
    seconds: float = 0.0
    sample_rate: int = 0
    peak: int = 0
    sum_squares: float = 0.0
    samples: int = 0

    def add(self, frame: InputAudioRawFrame) -> None:
        """Count one frame of 16-bit PCM."""
        self.frames += 1
        self.sample_rate = frame.sample_rate
        bytes_per_second = frame.sample_rate * frame.num_channels * 2
        if bytes_per_second > 0:
            self.seconds += len(frame.audio) / bytes_per_second
        pcm = array("h")
        pcm.frombytes(frame.audio[: len(frame.audio) // 2 * 2])
        if sys.byteorder == "big":  # the browser sends little-endian PCM
            pcm.byteswap()
        if pcm:
            self.peak = max(self.peak, max(pcm), -min(pcm))
            self.sum_squares += sum(s * s for s in pcm)
            self.samples += len(pcm)

    @property
    def silent(self) -> bool:
        """Whether nothing in the turn is loud enough to be speech (no audio at all included)."""
        return dbfs(self.peak) < SILENT_PEAK_DBFS

    def describe(self) -> str:
        """One log line of numbers, e.g. "3.2 s of audio in 13 frame(s) at 16000 Hz, peak -9 dBFS, rms -31 dBFS"."""
        if not self.frames:
            return "no audio from the browser while the mic was pressed"
        rms = math.sqrt(self.sum_squares / self.samples) if self.samples else 0.0
        return (
            f"{self.seconds:.1f} s of audio in {self.frames} frame(s) at {self.sample_rate} Hz, "
            f"peak {dbfs(self.peak):.0f} dBFS, rms {dbfs(rms):.0f} dBFS"
        )


def requested_talking(frame: RTVIClientMessageFrame) -> bool | None:
    """What a push-to-talk message asks for: True (start talking), False (stop), None (not a push-to-talk message).

    Only a literal `true` starts a turn; anything else in a push-to-talk message (false, missing, malformed) stops it,
    so a garbled message can never leave the mic open.
    """
    if frame.type != PUSH_TO_TALK_MESSAGE:
        return None
    return isinstance(frame.data, Mapping) and frame.data.get("talking") is True


def requested_cancel(frame: RTVIClientMessageFrame) -> bool:
    """Whether a push-to-talk message is the user's stop: end the answer, and take nothing more from the turn."""
    return requested_talking(frame) is False and isinstance(frame.data, Mapping) and frame.data.get("cancel") is True


class PushToTalkProcessor(FrameProcessor):
    """Starts and ends the user's voice turn on the client's push-to-talk messages instead of on detected speech."""

    def __init__(self, *, max_turn_secs: float = DEFAULT_MAX_TURN_SECS, **kwargs: Any) -> None:
        """`max_turn_secs` bounds one turn (seconds of audio received while talking)."""
        super().__init__(**kwargs)
        self._max_turn_secs = max_turn_secs
        self._talking = False
        self._turn = TurnAudio()
        self.last_turn: TurnAudio | None = None
        """The audio of the last closed turn, as logged."""

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
            elif requested_cancel(frame):
                await self._end_turn()  # the bridge drops its transcript (agent_bridge.py)
                await self.broadcast_interruption()
            elif talking is False:
                await self._end_turn()
        elif isinstance(frame, InputAudioRawFrame) and self._talking:
            self._turn.add(frame)
            if self._turn.seconds >= self._max_turn_secs:
                await self._end_turn(limit=True)

    async def _start_turn(self) -> None:
        if self._talking:
            return
        self._talking = True
        self._turn = TurnAudio()
        # Pressing to talk is the barge-in: stop the bot's audio and any reply still being prepared, then open the
        # segment. The interruption goes first so nothing it resets can drop the start of the new segment.
        await self.broadcast_interruption()
        await self.broadcast_frame(VADUserStartedSpeakingFrame)
        await self.broadcast_frame(UserStartedSpeakingFrame)

    async def _notify(self, reason: str) -> None:
        await self.push_frame(
            RTVIServerMessageFrame(data={"type": PUSH_TO_TALK_MESSAGE, "talking": False, "reason": reason})
        )

    async def _end_turn(self, *, limit: bool = False) -> None:
        if not self._talking:
            return
        self._talking = False
        self.last_turn = self._turn
        if self._turn.frames:
            logger.info("voice turn: {}", self._turn.describe())
        else:
            logger.warning("voice turn: {}", self._turn.describe())
        await self.broadcast_frame(VADUserStoppedSpeakingFrame)
        await self.broadcast_frame(UserStoppedSpeakingFrame)
        if limit:
            await self._notify("limit")
        if self._turn.silent:
            await self._notify("silent")
