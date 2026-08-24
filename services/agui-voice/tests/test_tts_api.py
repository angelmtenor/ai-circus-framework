"""Tests for api/tts.py's one-shot POST /tts/{scenario_slug} endpoint.

Runs the real Pipecat Pipeline/PipelineWorker/WorkerRunner machinery with a fake TTS
FrameProcessor standing in for a real provider (Piper/ElevenLabs/...), so the test
exercises the actual frame-collection/WAV-building plumbing without loading model
weights or hitting a network TTS API.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from ai_circus_shared.auth import Identity
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipecat.frames.frames import EndFrame, Frame, ManuallySwitchServiceFrame, TTSAudioRawFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import agui_voice.api.tts as tts_module
from agui_voice.api.tts import router, tts_endpoint
from agui_voice.core.identity import resolve_identity


class _FakeTTSService(FrameProcessor):
    """Stands in for a real TTSService: on TTSSpeakFrame, emits one audio chunk."""

    def __init__(self, **kwargs: object) -> None:
        """Track every frame type this instance actually receives, in order."""
        super().__init__(**kwargs)
        self.received: list[type[Frame]] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self.received.append(type(frame))
        if isinstance(frame, TTSSpeakFrame):
            await self.push_frame(TTSAudioRawFrame(audio=b"\x01\x00" * 8000, sample_rate=16000, num_channels=1))
        elif isinstance(frame, EndFrame):
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    """A TestClient with identity overridden and a fake TTS provider wired in."""
    monkeypatch.setattr(tts_module, "build_tts_service", lambda _config, provider=None: (_FakeTTSService(), {}))
    monkeypatch.setattr(tts_module, "resolve_active_providers", lambda _config: ("whisper", "piper"))
    monkeypatch.setattr(tts_module, "get_env_config", lambda: object())

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_tts_endpoint_returns_wav_audio(client: TestClient) -> None:
    """A successful call returns a playable WAV file built from the TTS provider's audio frames."""
    response = client.post("/tts/churn", json={"text": "hello there"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF"
    assert response.content[8:12] == b"WAVE"


def test_tts_endpoint_rejects_empty_text(client: TestClient) -> None:
    """Blank text is a 422, not a wasted call to the TTS provider."""
    response = client.post("/tts/churn", json={"text": "   "})

    assert response.status_code == 422


def test_tts_endpoint_is_registered_on_the_router() -> None:
    """Sanity check that the route function itself is the one mounted on the router."""
    assert any(getattr(r, "endpoint", None) is tts_endpoint for r in router.routes)


@pytest.fixture
def client_with_language_switch(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    """A TestClient whose TTS provider supports language switching (e.g. Piper),
    unlike the default `client` fixture above which mimics a provider that doesn't.
    """
    fake_service = _FakeTTSService()
    sentinel_en, sentinel_es = FrameProcessor(), FrameProcessor()
    monkeypatch.setattr(
        tts_module,
        "build_tts_service",
        lambda _config, provider=None: (fake_service, {"en": sentinel_en, "es": sentinel_es}),
    )
    monkeypatch.setattr(tts_module, "resolve_active_providers", lambda _config: ("whisper", "piper"))
    monkeypatch.setattr(tts_module, "get_env_config", lambda: object())

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})
    )
    app.state.fake_service = fake_service
    app.state.sentinel_en = sentinel_en
    app.state.sentinel_es = sentinel_es
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_tts_endpoint_switches_to_the_spanish_voice_for_spanish_text(
    client_with_language_switch: TestClient,
) -> None:
    """Spanish text must explicitly switch the ServiceSwitcher to the Spanish voice
    — without this, a fresh switcher always starts on its first-listed ("en")
    service regardless of what the text says (this is the /tts endpoint's own bug:
    it has no STT stage to get a real detected language from, unlike the live WS
    pipeline).
    """
    response = client_with_language_switch.post("/tts/churn", json={"text": "¿Cómo estás? Muchas gracias"})

    assert response.status_code == 200
    app = client_with_language_switch.app
    switch_frames = [f for f in app.state.fake_service.received if f is ManuallySwitchServiceFrame]
    assert switch_frames, app.state.fake_service.received


def test_tts_endpoint_switches_to_the_english_voice_for_english_text(
    client_with_language_switch: TestClient,
) -> None:
    """English text explicitly switches to the English voice too, rather than
    relying on it happening to already be the switcher's default active service.
    """
    response = client_with_language_switch.post("/tts/churn", json={"text": "Hello, thanks for your help"})

    assert response.status_code == 200
    app = client_with_language_switch.app
    switch_frames = [f for f in app.state.fake_service.received if f is ManuallySwitchServiceFrame]
    assert switch_frames, app.state.fake_service.received
