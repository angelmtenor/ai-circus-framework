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

from agui_voice.core.push_to_talk import (
    PUSH_TO_TALK_MESSAGE,
    PushToTalkProcessor,
    TurnAudio,
    requested_cancel,
    requested_talking,
)

SAMPLE_RATE = 16_000


class Recorder:
    """What the processor pushed (forwarded or its own) and broadcast, in order."""

    def __init__(self) -> None:
        """Start with nothing pushed or broadcast."""
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
    the start of the new turn; the message itself is still forwarded.
    """
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


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"talking": False, "cancel": True}, True),
        ({"talking": True, "cancel": True}, False),
        ({"talking": False, "cancel": "yes"}, False),
        ({"talking": False}, False),
        (None, False),
    ],
)
def test_requested_cancel(data: Any, expected: bool) -> None:
    assert requested_cancel(_ptt(data)) is expected


async def test_the_users_stop_ends_the_answer_and_opens_nothing(recorder: Recorder) -> None:
    """A press while waiting used to start another turn: pressing to stop recorded a second of nothing instead."""
    processor = _processor(recorder)

    await _send(processor, _ptt({"talking": False, "cancel": True}))

    assert recorder.events == ["interruption"]
    assert processor.talking is False


async def test_a_stop_while_the_turn_is_still_open_closes_it_too(recorder: Recorder) -> None:
    processor = _processor(recorder)
    await _send(processor, _ptt({"talking": True}))
    recorder.events.clear()

    await _send(processor, _ptt({"talking": False, "cancel": True}))

    assert recorder.events == ["VADUserStoppedSpeakingFrame", "UserStoppedSpeakingFrame", "interruption"]
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

    await _send(processor, _tone(0.6, 3277))
    assert processor.talking is True

    last = _tone(0.6, 3277)
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


def _tone(seconds: float, amplitude: int) -> InputAudioRawFrame:
    """A square wave at `amplitude` (little-endian 16-bit), so peak and RMS are both `amplitude`."""
    samples = int(SAMPLE_RATE * seconds)
    pcm = b"".join(((amplitude if i % 2 else -amplitude).to_bytes(2, "little", signed=True)) for i in range(samples))
    return InputAudioRawFrame(audio=pcm, sample_rate=SAMPLE_RATE, num_channels=1)


async def test_a_closed_turn_reports_how_much_audio_came_and_how_loud(recorder: Recorder) -> None:
    """The numbers that tell a silent or missing mic from speech the STT could not read; never the audio itself."""
    processor = _processor(recorder)
    await _send(processor, _ptt({"talking": True}))
    await _send(processor, _tone(0.5, 3277))  # -20 dBFS
    await _send(processor, _tone(0.5, 3277))
    await _send(processor, _ptt({"talking": False}))

    turn = processor.last_turn
    assert turn is not None
    assert (turn.frames, turn.sample_rate) == (2, SAMPLE_RATE)
    assert turn.seconds == pytest.approx(1.0)
    assert turn.describe() == "1.0 s of audio in 2 frame(s) at 16000 Hz, peak -20 dBFS, rms -20 dBFS"


async def test_a_turn_without_audio_says_the_browser_sent_none(recorder: Recorder) -> None:
    processor = _processor(recorder)
    await _send(processor, _audio(2.0))  # before the press: not this turn's audio
    await _send(processor, _ptt({"talking": True}))
    await _send(processor, _ptt({"talking": False}))

    assert processor.last_turn is not None
    assert processor.last_turn.describe() == "no audio from the browser while the mic was pressed"


def _notices(recorder: Recorder) -> list[Any]:
    return [frame.data for frame in recorder.pushed if isinstance(frame, RTVIServerMessageFrame)]


SILENT_NOTICE = {"type": PUSH_TO_TALK_MESSAGE, "talking": False, "reason": "silent"}


@pytest.mark.parametrize(
    ("frames", "told"),
    [
        ([], True),  # the browser sent nothing
        ([_audio(1.0)], True),  # a muted microphone: zeros
        ([_tone(1.0, 8)], True),  # -72 dBFS
        ([_tone(1.0, 520)], False),  # quiet speech, -36 dBFS
        ([_audio(1.0), _tone(0.2, 3277)], False),  # a pause, then speech
    ],
)
async def test_a_silent_turn_is_told_to_the_client(
    recorder: Recorder, frames: list[InputAudioRawFrame], told: bool
) -> None:
    """Nothing will be transcribed, so the client would wait 30 s for an answer without saying why; the turn still
    closes as any other, and the notice comes after that.
    """
    processor = _processor(recorder)
    await _send(processor, _ptt({"talking": True}))
    for frame in frames:
        await _send(processor, frame)
    await _send(processor, _ptt({"talking": False}))

    assert _notices(recorder) == ([SILENT_NOTICE] if told else [])
    assert recorder.events[-2:] == ["VADUserStoppedSpeakingFrame", "UserStoppedSpeakingFrame"]


async def test_a_silent_turn_past_the_limit_says_both_the_limit_first(recorder: Recorder) -> None:
    """The client stops sending on the limit notice, then shows why nothing was heard."""
    processor = _processor(recorder, max_turn_secs=1.0)
    await _send(processor, _ptt({"talking": True}))
    await _send(processor, _audio(1.2))

    assert [notice["reason"] for notice in _notices(recorder)] == ["limit", "silent"]


def test_silence_reads_as_minus_infinity() -> None:
    turn = TurnAudio()
    turn.add(_audio(0.5))
    assert turn.describe() == "0.5 s of audio in 1 frame(s) at 16000 Hz, peak -inf dBFS, rms -inf dBFS"


def test_the_frames_it_replaces_the_vad_with_are_the_ones_whisper_segments_on() -> None:
    """Whisper (a SegmentedSTTService) buffers between exactly these two frames; if Pipecat renames them, this
    processor silently stops bounding turns.
    """
    from pipecat.services.stt_service import SegmentedSTTService

    source = SegmentedSTTService.process_frame.__code__.co_names
    assert VADUserStartedSpeakingFrame.__name__ in source
    assert VADUserStoppedSpeakingFrame.__name__ in source
