"""Tests for core/agent_bridge.py's AgentBridgeProcessor — the Pipecat "LLM stage"
that calls the existing assistant/rag-agent/form-agent `/agui/{scenario_slug}` SSE
endpoint instead of running an LLM directly. `push_frame` and `task_manager` are
monkeypatched on the instance so these tests exercise the SSE parsing and message
bookkeeping without a real Pipecat pipeline/transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import pytest
from ag_ui.core import UserMessage
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMTextFrame,
    ManuallySwitchServiceFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transcriptions.language import Language

from agui_voice.core.agent_bridge import AgentBridgeProcessor

MockHandler = Callable[[httpx.Request], Awaitable[httpx.Response]]
PushedFrame = tuple[Frame, FrameDirection]


def _sse_response(handler: MockHandler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _sse_lines(*events: str) -> bytes:
    return "".join(f"data: {event}\n\n" for event in events).encode()


@pytest.fixture
def pushed_frames() -> list[PushedFrame]:
    return []


@pytest.fixture
def bridge(pushed_frames: list[PushedFrame]) -> AgentBridgeProcessor:
    async def fake_push_frame(  # ruff: ignore[unused-async] (overrides the real async push_frame method)
        frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        pushed_frames.append((frame, direction))

    async def handler(_request: httpx.Request) -> httpx.Response:  # ruff: ignore[unused-async] (httpx.MockTransport requires an async handler)
        raise AssertionError("test must override the http_client's transport")

    processor = AgentBridgeProcessor(
        upstream_url="http://assistant:8000/agui/churn",
        scenario_slug="churn",
        authorization="Bearer test-token",
        http_client=_sse_response(handler),
    )
    processor.push_frame = fake_push_frame
    return processor


async def test_run_agent_streams_text_deltas_and_records_assistant_reply(
    bridge: AgentBridgeProcessor, pushed_frames: list[PushedFrame]
) -> None:
    """A successful SSE run pushes Start -> LLMTextFrame deltas -> End, and appends
    the full assistant reply to the running message history.

    Pushing `LLMTextFrame` specifically (not the base `TextFrame`) matters beyond
    typing: Pipecat's RTVIObserver only converts `LLMTextFrame` into the
    `bot-llm-text` wire message clients render as the response transcript — a plain
    `TextFrame` still reaches TTS (LLMTextFrame is a TextFrame subclass) but is
    silently invisible to RTVI, so the response would only ever be heard, not shown.
    """

    async def handler(request: httpx.Request) -> httpx.Response:  # ruff: ignore[unused-async]
        assert request.headers["authorization"] == "Bearer test-token"
        body = _sse_lines(
            '{"type": "TEXT_MESSAGE_CONTENT", "delta": "Hello"}',
            '{"type": "TEXT_MESSAGE_CONTENT", "delta": " world"}',
            '{"type": "RUN_FINISHED"}',
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    bridge._client = _sse_response(handler)
    bridge._messages.append(UserMessage(id="u1", content="hi"))  # simulate process_frame's TranscriptionFrame handling

    await bridge._run_agent()

    frame_types = [type(frame).__name__ for frame, _ in pushed_frames]
    assert frame_types == ["LLMFullResponseStartFrame", "LLMTextFrame", "LLMTextFrame", "LLMFullResponseEndFrame"]
    assert [f.text for f, _ in pushed_frames if isinstance(f, LLMTextFrame)] == ["Hello", " world"]
    assert bridge._messages[-1].role == "assistant"
    assert bridge._messages[-1].content == "Hello world"


async def test_run_agent_handles_run_error_without_crashing(
    bridge: AgentBridgeProcessor, pushed_frames: list[PushedFrame]
) -> None:
    """A RUN_ERROR event is logged, not raised, and the response cycle still ends cleanly."""

    async def handler(_request: httpx.Request) -> httpx.Response:  # ruff: ignore[unused-async]
        body = _sse_lines('{"type": "RUN_ERROR", "message": "boom"}')
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    bridge._client = _sse_response(handler)

    await bridge._run_agent()

    frame_types = [type(frame).__name__ for frame, _ in pushed_frames]
    assert frame_types == ["LLMFullResponseStartFrame", "LLMFullResponseEndFrame"]
    # No text was produced, so no assistant message is recorded.
    assert bridge._messages == []


async def test_run_agent_handles_transport_failure_without_crashing(
    bridge: AgentBridgeProcessor, pushed_frames: list[PushedFrame]
) -> None:
    """A network-level failure talking to the upstream agent doesn't propagate — it's
    logged and the pipeline still gets a clean Start/End pair.
    """

    async def handler(_request: httpx.Request) -> httpx.Response:  # ruff: ignore[unused-async]
        raise httpx.ConnectError("connection refused")

    bridge._client = _sse_response(handler)

    await bridge._run_agent()

    frame_types = [type(frame).__name__ for frame, _ in pushed_frames]
    assert frame_types == ["LLMFullResponseStartFrame", "LLMFullResponseEndFrame"]


async def test_process_frame_passes_through_unrelated_frames(
    bridge: AgentBridgeProcessor, pushed_frames: list[PushedFrame]
) -> None:
    """Frame types this processor doesn't care about are forwarded unchanged."""
    frame = TextFrame(text="unrelated")

    await bridge.process_frame(frame, FrameDirection.UPSTREAM)

    assert pushed_frames == [(frame, FrameDirection.UPSTREAM)]


async def test_process_frame_ignores_blank_transcription(
    bridge: AgentBridgeProcessor, pushed_frames: list[PushedFrame]
) -> None:
    """A TranscriptionFrame with only whitespace doesn't start an agent run."""
    frame = TranscriptionFrame(text="   ", user_id="u", timestamp="now")

    await bridge.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert bridge._messages == []
    assert bridge._inflight is None


async def test_process_frame_starts_agent_run_on_final_transcription(
    bridge: AgentBridgeProcessor, pushed_frames: list[PushedFrame], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-blank TranscriptionFrame appends a user message and kicks off _run_agent
    via the pipeline's task manager.
    """
    created: list[tuple[asyncio.Task, str]] = []

    class FakeTaskManager:
        def create_task(self, coroutine: Awaitable[None], name: str) -> asyncio.Task:
            task = asyncio.ensure_future(coroutine)
            created.append((task, name))
            return task

        async def cancel_task(self, task: asyncio.Task, timeout: float | None = None) -> None:
            task.cancel()

    monkeypatch.setattr(type(bridge), "task_manager", property(lambda self: FakeTaskManager()))

    async def handler(_request: httpx.Request) -> httpx.Response:  # ruff: ignore[unused-async]
        return httpx.Response(
            200, content=_sse_lines('{"type": "RUN_FINISHED"}'), headers={"content-type": "text/event-stream"}
        )

    bridge._client = _sse_response(handler)
    frame = TranscriptionFrame(text="hello there", user_id="u", timestamp="now")

    await bridge.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert created
    await created[0][0]

    assert bridge._messages[0].role == "user"
    assert bridge._messages[0].content == "hello there"


async def test_process_frame_cancels_inflight_run_on_interruption(
    bridge: AgentBridgeProcessor, pushed_frames: list[PushedFrame], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An InterruptionFrame cancels the in-flight upstream call (barge-in) and forwards
    the frame downstream so TTS/transport can react too.
    """
    cancelled: list[object] = []

    class FakeTaskManager:
        def create_task(self, coroutine: Awaitable[None], *_args: object, **_kwargs: object) -> asyncio.Task:
            # The base FrameProcessor.process_frame() also reacts to InterruptionFrame
            # internally (independent of AgentBridgeProcessor's own handling below) and
            # schedules its own bookkeeping task via task_manager.create_task — needed
            # here purely so that call doesn't blow up, not exercised by this test.
            return asyncio.ensure_future(coroutine)

        async def cancel_task(self, task: object, *_args: object, **_kwargs: object) -> None:
            cancelled.append(task)

    monkeypatch.setattr(type(bridge), "task_manager", property(lambda self: FakeTaskManager()))
    fake_task = object()
    bridge._inflight = fake_task
    frame = InterruptionFrame()

    await bridge.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert cancelled == [fake_task]
    assert bridge._inflight is None
    assert pushed_frames == [(frame, FrameDirection.DOWNSTREAM)]


class _FakeTaskManager:
    """Runs coroutines as real asyncio tasks without pipecat's own task bookkeeping."""

    def create_task(self, coroutine: Awaitable[None], *_args: object, **_kwargs: object) -> asyncio.Task:
        return asyncio.ensure_future(coroutine)

    async def cancel_task(self, task: asyncio.Task, *_args: object, **_kwargs: object) -> None:
        task.cancel()


def _bridge_with_language_switch(
    pushed_frames: list[PushedFrame], monkeypatch: pytest.MonkeyPatch, tts_language_switch: dict[str, object]
) -> AgentBridgeProcessor:
    async def fake_push_frame(  # ruff: ignore[unused-async] (overrides the real async push_frame method)
        frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        pushed_frames.append((frame, direction))

    async def handler(_request: httpx.Request) -> httpx.Response:  # ruff: ignore[unused-async]
        return httpx.Response(
            200, content=_sse_lines('{"type": "RUN_FINISHED"}'), headers={"content-type": "text/event-stream"}
        )

    bridge = AgentBridgeProcessor(
        upstream_url="http://assistant:8000/agui/churn",
        scenario_slug="churn",
        authorization="Bearer test-token",
        http_client=_sse_response(handler),
        tts_language_switch=tts_language_switch,
    )
    bridge.push_frame = fake_push_frame
    monkeypatch.setattr(type(bridge), "task_manager", property(lambda self: _FakeTaskManager()))
    return bridge


async def test_final_transcription_switches_tts_language_on_change(
    pushed_frames: list[PushedFrame], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A final TranscriptionFrame whose detected language differs from the current
    one pushes a ManuallySwitchServiceFrame for that language's preloaded TTS
    service — but a second utterance in the *same* language doesn't push again.
    """
    spanish_service, english_service = object(), object()
    bridge = _bridge_with_language_switch(pushed_frames, monkeypatch, {"en": english_service, "es": spanish_service})

    frame = TranscriptionFrame(text="Hola, ¿cómo estás?", user_id="u", timestamp="now", language=Language.ES)
    await bridge.process_frame(frame, FrameDirection.DOWNSTREAM)
    await bridge._inflight

    switch_frames = [f for f, _ in pushed_frames if isinstance(f, ManuallySwitchServiceFrame)]
    assert len(switch_frames) == 1
    assert switch_frames[0].service is spanish_service
    assert bridge._current_tts_language == "es"

    frame2 = TranscriptionFrame(text="¿Todo bien?", user_id="u", timestamp="now", language=Language.ES)
    await bridge.process_frame(frame2, FrameDirection.DOWNSTREAM)
    await bridge._inflight

    switch_frames = [f for f, _ in pushed_frames if isinstance(f, ManuallySwitchServiceFrame)]
    assert len(switch_frames) == 1  # still just the one switch — same language, no-op


async def test_final_transcription_ignores_language_with_no_configured_voice(
    pushed_frames: list[PushedFrame], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A detected language this deployment has no TTS voice for is left alone —
    stays on whichever service was already active rather than erroring.
    """
    bridge = _bridge_with_language_switch(pushed_frames, monkeypatch, {"en": object(), "es": object()})

    frame = TranscriptionFrame(text="Bonjour", user_id="u", timestamp="now", language=Language.FR)
    await bridge.process_frame(frame, FrameDirection.DOWNSTREAM)
    await bridge._inflight

    assert not any(isinstance(f, ManuallySwitchServiceFrame) for f, _ in pushed_frames)
    assert bridge._current_tts_language is None


async def test_final_transcription_does_nothing_without_a_switch_map(
    bridge: AgentBridgeProcessor, pushed_frames: list[PushedFrame], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider that doesn't support language switching (the default `bridge`
    fixture has no `tts_language_switch`) never pushes a switch frame, regardless
    of detected language — e.g. ElevenLabs/Cartesia, see providers.py.
    """
    monkeypatch.setattr(type(bridge), "task_manager", property(lambda self: _FakeTaskManager()))

    async def handler(_request: httpx.Request) -> httpx.Response:  # ruff: ignore[unused-async]
        return httpx.Response(
            200, content=_sse_lines('{"type": "RUN_FINISHED"}'), headers={"content-type": "text/event-stream"}
        )

    bridge._client = _sse_response(handler)
    frame = TranscriptionFrame(text="Hola", user_id="u", timestamp="now", language=Language.ES)

    await bridge.process_frame(frame, FrameDirection.DOWNSTREAM)
    await bridge._inflight

    assert not any(isinstance(f, ManuallySwitchServiceFrame) for f, _ in pushed_frames)
