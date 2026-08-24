"""
- Title:    One-shot text-to-speech endpoint (the chat UI's loudspeaker icon)
- Author:   ai-circus-framework contributors

Decoupled from the live voice WebSocket pipeline (`api/ws.py`): this is a plain
"text in, audio out" HTTP call with no STT/turn-taking, so it runs a minimal
one-processor Pipecat pipeline (`TTSSpeakFrame -> TTS -> sink`) instead. Reuses the
same `build_tts_service` provider factory so both features stay on the same
STT/TTS_PROVIDER configuration.

The full reply is buffered server-side and returned as one proper WAV file rather
than streamed chunk-by-chunk: a browser `<audio>` element can't play headerless raw
PCM incrementally without MediaSource Extensions, and a chat reply is short enough
that buffering costs nothing noticeable.
"""

from __future__ import annotations

import asyncio
import wave
from io import BytesIO
from typing import Any

from ai_circus_shared.auth import Identity
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pipecat.frames.frames import EndFrame, Frame, ManuallySwitchServiceFrame, TTSAudioRawFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner
from pydantic import BaseModel

from agui_voice import get_env_config
from agui_voice.core.identity import resolve_identity
from agui_voice.core.providers import build_tts_service, guess_text_language, resolve_active_providers

router = APIRouter()


class TTSRequest(BaseModel):
    """Request body for POST /tts/{scenario_slug}."""

    text: str


class _AudioCollector(FrameProcessor):
    """Pipeline sink that buffers every `TTSAudioRawFrame` in memory and resolves
    `done` (an `asyncio.Event`) once the pipeline's `EndFrame` reaches it.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Start with an empty buffer and an unset completion event."""
        super().__init__(**kwargs)
        self.chunks: list[bytes] = []
        self.sample_rate: int | None = None
        self.num_channels: int | None = None
        self.done = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.chunks.append(frame.audio)
            self.sample_rate = frame.sample_rate
            self.num_channels = frame.num_channels
        elif isinstance(frame, EndFrame):
            self.done.set()
        # Every frame — including StartFrame/EndFrame — must still reach the
        # worker's own sink, or PipelineWorker's startup/shutdown bookkeeping (it
        # waits for StartFrame/EndFrame to arrive at the end of the pipeline) hangs
        # forever: this being the last processor before that sink doesn't exempt it
        # from forwarding.
        await self.push_frame(frame, direction)


@router.post("/tts/{scenario_slug}")
async def tts_endpoint(
    scenario_slug: str,
    body: TTSRequest,
    identity: Identity = Depends(resolve_identity),
) -> Response:
    """Speak `body.text` and return the full reply as a WAV file.

    Entitlement-checks `scenario_slug` via `resolve_identity` even though speech
    synthesis itself touches no scenario data — defense-in-depth consistency with
    every other org_id-scoped route in this platform, not a real data-access need.
    """
    assert identity.org_id is not None  # resolve_identity() already guarantees this
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty.")

    config = get_env_config()
    collector = _AudioCollector()
    # Off the event loop for the same reason as api/ws.py's voice_ws — a cold-start
    # TTS provider construction (downloading/loading model weights) is blocking work
    # that would otherwise stall every concurrent request to this instance.
    # The admin Settings page's live choice (persisted in platform-registry), falling
    # back to this instance's static TTS_PROVIDER — see resolve_active_providers'
    # docstring. Only the tts_provider half of the pair matters here (this endpoint
    # has no STT stage), but resolving both keeps one code path with api/ws.py.
    _stt_provider, tts_provider = await run_in_threadpool(resolve_active_providers, config)
    tts_service, tts_language_switch = await run_in_threadpool(build_tts_service, config, provider=tts_provider)
    pipeline = Pipeline([tts_service, collector])
    worker = PipelineWorker(pipeline)
    runner = WorkerRunner(handle_sigint=False)

    frames: list[Frame] = []
    if tts_language_switch:
        # This endpoint has no STT stage to get a real detected language from (see
        # api/ws.py's AgentBridgeProcessor for that), so guess from the text itself
        # — a fresh ServiceSwitcher always starts on its first-listed language
        # ("en"), so Spanish text needs an explicit switch or it would come out in
        # the English voice regardless of what the text actually says.
        target_service = tts_language_switch.get(guess_text_language(body.text))
        if target_service is not None:
            frames.append(ManuallySwitchServiceFrame(service=target_service))
    frames.extend([TTSSpeakFrame(text=body.text), EndFrame()])

    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    await worker.queue_frames(frames)
    await collector.done.wait()
    await run_task

    if not collector.chunks:
        raise HTTPException(status_code=502, detail="TTS provider returned no audio.")

    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(collector.num_channels or 1)
        wav_file.setsampwidth(2)  # pipecat TTS services emit 16-bit PCM
        wav_file.setframerate(collector.sample_rate or 24000)
        wav_file.writeframes(b"".join(collector.chunks))

    return Response(content=buffer.getvalue(), media_type="audio/wav")
