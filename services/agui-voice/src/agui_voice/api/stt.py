"""
- Title:    One-shot speech-to-text endpoint (the chat's dictation mic)
- Author:   Angel Martinez-Tenor

The opposite of `api/tts.py`: audio in, text out, with no agent and no pipeline. The browser records between two presses
of the mic, posts the recording here and puts the text in the chat's input, where the user reads, edits and sends it as
any typed message (docs/adr/0033). It uses the STT provider picked in Settings, as the live voice mode does.
"""

from __future__ import annotations

from ai_circus_shared.auth import Identity
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from agui_voice import get_env_config
from agui_voice.core.identity import resolve_identity
from agui_voice.core.providers import resolve_active_providers, transcribe_recording
from agui_voice.core.speech import SAMPLE_RATE, wav_pcm

router = APIRouter()

MAX_DICTATION_SECONDS = 120
"""The longest recording taken; ui-react's useDictation.ts stops at the same point."""

# 16-bit mono PCM, plus room for a WAV header.
MAX_DICTATION_BYTES = MAX_DICTATION_SECONDS * SAMPLE_RATE * 2 + 4096


class STTResponse(BaseModel):
    """What was said. `text` is empty when nothing was, and `reason` then says why: `silent` (no sound at all: a muted
    or wrong microphone) or `no-speech` (sound, but no words).
    """

    text: str
    language: str | None = None
    reason: str | None = None


async def _bounded_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_DICTATION_BYTES:
            raise HTTPException(status_code=413, detail=f"recording longer than {MAX_DICTATION_SECONDS} s.")
    return bytes(body)


@router.post("/stt/{scenario_slug}")
async def stt_endpoint(
    scenario_slug: str, request: Request, identity: Identity = Depends(resolve_identity)
) -> STTResponse:
    """Transcribe the posted recording (a mono 16-bit WAV at 16 kHz); entitlement-checked as `/tts` is."""
    assert identity.org_id is not None  # resolve_identity() already guarantees this
    audio = await _bounded_body(request)
    try:
        pcm = wav_pcm(audio)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    config = get_env_config()
    stt_provider, _tts_provider = await run_in_threadpool(resolve_active_providers, config)
    heard = await transcribe_recording(config, pcm, provider=stt_provider)
    return STTResponse(text=heard.text, language=heard.language, reason=heard.reason)
