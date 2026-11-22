"""Tests for core/push_to_talk.py's PushToTalkProcessor — the stage that replaced Silero's VADProcessor: the user's
press and release (RTVI `client-message`s of type `push-to-talk`) bound each voice turn. `push_frame`,
`broadcast_frame` and `broadcast_interruption` are replaced on the instance, so these tests read what the processor
emits without a running Pipecat pipeline.
"""

from __future__ import annotations

from typing import Any

import pytest
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame, RTVIServerMessageFrame

from agui_voice.core.push_to_talk import PUSH_TO_TALK_MESSAGE, PushToTalkProcessor, requested_talking

SAMPLE_RATE = 16_000


class Recorder:
    """What the processor pushed (forwarded or its own) and broadcast, in order."""

    def __init__(self) -> None:
        self.pushed: list[Frame] = []
        self.events: list[str] = []


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


def _processor(recorder: Recorder, **kwargs: Any) -> PushToTalkProcessor:
    processor = PushToTalkProcessor(**kwargs)

    async def fake_push_frame(  # ruff: ignore[unused-async] (overrides the real async push_frame method)
        frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        recorder.pushed.append(frame)

    async def fake_broadcast_frame(frame_cls: type[Frame], **_kwargs: Any) -> None:  # ruff: ignore[unused-async]
        recorder.events.append(frame_cls.__name__)

    async def fake_broadcast_interruption() -> None:  # ruff: ignore[unused-async]
        recorder.events.append("interruption")

    processor.push_frame = fake_push_frame
    processor.broadcast_frame = fake_broadcast_frame
    processor.broadcast_interruption = fake_broadcast_interruption
    return processor


def _ptt(data: Any) -> RTVIClientMessageFrame:
    return RTVIClientMessageFrame(msg_id="m-1", type=PUSH_TO_TALK_MESSAGE, data=data)


def _audio(seconds: float) -> InputAudioRawFrame:
    return InputAudioRawFrame(audio=bytes(int(SAMPLE_RATE * seconds) * 2), sample_rate=SAMPLE_RATE, num_channels=1)


async def _send(processor: PushToTalkProcessor, frame: Frame) -> None:
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (_ptt({"talking": True}), True),
        (_ptt({"talking": False}), False),
        # Anything but a literal true stops: a garbled message must never leave the mic open.
        (_ptt({"talking": "yes"}), False),
        (_ptt({}), False),
        (_ptt(None), False),
        (_ptt(["talking"]), False),
        (RTVIClientMessageFrame(msg_id="m-2", type="something-else", data={"talking": True}), None),
    ],
)
def test_requested_talking(frame: RTVIClientMessageFrame, expected: bool | None) -> None:
    assert requested_talking(frame) is expected


async def test_a_press_interrupts_the_bot_then_opens_the_turn(recorder: Recorder) -> None:
    """Pressing is the barge-in: the interruption goes out before the segment opens, so nothing it resets can drop
    the start of the new turn; the message itself is still forwarded."""
    processor = _processor(recorder)
    press = _ptt({"talking": True})

    await _send(processor, press)

    assert recorder.events == ["interruption", "VADUserStartedSpeakingFrame", "UserStartedSpeakingFrame"]
    assert recorder.pushed == [press]
    assert processor.talking is True


async def test_a_release_closes_the_turn_so_the_stt_transcribes_it(recorder: Recorder) -> None:
    processor = _processor(recorder)
    await _send(processor, _ptt({"talking": True}))
    recorder.events.clear()

    await _send(processor, _ptt({"talking": False}))

    assert recorder.events == ["VADUserStoppedSpeakingFrame", "UserStoppedSpeakingFrame"]
    assert processor.talking is False


async def test_repeated_presses_and_a_release_without_a_press_change_nothing(recorder: Recorder) -> None:
    processor = _processor(recorder)

    await _send(processor, _ptt({"talking": False}))
    assert recorder.events == []

    await _send(processor, _ptt({"talking": True}))
    await _send(processor, _ptt({"talking": True}))
    assert recorder.events.count("VADUserStartedSpeakingFrame") == 1
    assert recorder.events.count("interruption") == 1


async def test_other_client_messages_are_only_forwarded(recorder: Recorder) -> None:
    processor = _processor(recorder)
    other = RTVIClientMessageFrame(msg_id="m-3", type="something-else", data={"talking": True})

    await _send(processor, other)

    assert recorder.pushed == [other]
    assert recorder.events == []
    assert processor.talking is False


async def test_nothing_ends_a_turn_on_silence(recorder: Recorder) -> None:
    """No voice-activity detection: seconds of (silent) audio inside the limit keep the turn open."""
    processor = _processor(recorder)
    await _send(processor, _ptt({"talking": True}))

    for _ in range(10):
        await _send(processor, _audio(1.0))

    assert processor.talking is True
    assert "VADUserStoppedSpeakingFrame" not in recorder.events
    assert all(isinstance(frame, (RTVIClientMessageFrame, InputAudioRawFrame)) for frame in recorder.pushed)


async def test_a_turn_past_the_limit_ends_and_tells_the_client(recorder: Recorder) -> None:
    """A stuck key or a client that never sends the stop cannot grow the STT buffer without limit."""
    processor = _processor(recorder, max_turn_secs=1.0)
    await _send(processor, _ptt({"talking": True}))

    await _send(processor, _audio(0.6))
    assert processor.talking is True

    last = _audio(0.6)
    await _send(processor, last)

    assert processor.talking is False
    assert recorder.events[-2:] == ["VADUserStoppedSpeakingFrame", "UserStoppedSpeakingFrame"]
    # The audio that crossed the limit is forwarded first (it belongs to the turn), then the client is told.
    assert recorder.pushed[-2] is last
    notice = recorder.pushed[-1]
    assert isinstance(notice, RTVIServerMessageFrame)
    assert notice.data == {"type": PUSH_TO_TALK_MESSAGE, "talking": False, "reason": "limit"}


async def test_audio_outside_a_turn_does_not_count_towards_the_limit(recorder: Recorder) -> None:
    processor = _processor(recorder, max_turn_secs=1.0)

    await _send(processor, _audio(5.0))
    await _send(processor, _ptt({"talking": True}))
    await _send(processor, _audio(0.5))

    assert processor.talking is True


async def test_each_turn_gets_its_own_limit(recorder: Recorder) -> None:
    processor = _processor(recorder, max_turn_secs=1.0)
    await _send(processor, _ptt({"talking": True}))
    await _send(processor, _audio(0.8))
    await _send(processor, _ptt({"talking": False}))

    await _send(processor, _ptt({"talking": True}))
    await _send(processor, _audio(0.8))

    assert processor.talking is True


def test_the_frames_it_replaces_the_vad_with_are_the_ones_whisper_segments_on() -> None:
    """Whisper (a SegmentedSTTService) buffers between exactly these two frames; if Pipecat renames them, this
    processor silently stops bounding turns."""
    from pipecat.services.stt_service import SegmentedSTTService

    source = SegmentedSTTService.process_frame.__code__.co_names
    assert VADUserStartedSpeakingFrame.__name__ in source
    assert VADUserStoppedSpeakingFrame.__name__ in source
