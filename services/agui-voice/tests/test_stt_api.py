"""Tests for api/stt.py's POST /stt/{scenario_slug} (the chat's dictation) and the transcription behind it."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from ai_circus_shared.auth import Identity
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

import agui_voice.api.stt as stt_module
import agui_voice.core.providers as providers_module
from agui_voice.core.identity import resolve_identity
from agui_voice.core.speech import SAMPLE_RATE, samples_wav

SPEECH = np.full(SAMPLE_RATE, 0.1, dtype=np.float32)


class _LocalModel:
    def __init__(self, text: str = " Hola desde aquí") -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, audio: object, **kwargs: object) -> tuple[object, object]:
        self.calls += 1
        return iter([SimpleNamespace(text=self.text, no_speech_prob=0.1)]), SimpleNamespace(language="es")


@pytest.fixture
def local(monkeypatch: pytest.MonkeyPatch) -> _LocalModel:
    model = _LocalModel()
    monkeypatch.setattr(providers_module, "_cached_whisper_model", lambda *_args: model)
    monkeypatch.setattr(providers_module, "trim_to_speech", lambda samples: samples)
    return model


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, local: _LocalModel) -> Generator[TestClient]:
    config = SimpleNamespace(WHISPER_MODEL="base", GROQ_API_KEY=SecretStr("gsk"))
    monkeypatch.setattr(stt_module, "get_env_config", lambda: config)
    monkeypatch.setattr(stt_module, "resolve_active_providers", lambda _config: ("whisper", "piper"))
    app = FastAPI()
    app.include_router(stt_module.router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})
    )
    yield TestClient(app)


def _post(client: TestClient, samples: np.ndarray = SPEECH) -> Any:
    return client.post("/stt/churn", content=samples_wav(samples), headers={"Content-Type": "audio/wav"})


def test_a_recording_comes_back_as_text_with_its_language(client: TestClient) -> None:
    response = _post(client)
    assert response.status_code == 200
    assert response.json() == {"text": "Hola desde aquí", "language": "es", "reason": None}


def test_a_silent_recording_is_not_transcribed(client: TestClient, local: _LocalModel) -> None:
    assert _post(client, np.zeros(SAMPLE_RATE, dtype=np.float32)).json()["reason"] == "silent"
    assert local.calls == 0


def test_a_recording_without_words_is_not_transcribed(
    client: TestClient, local: _LocalModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A click or a breath: Whisper would answer "Thank you." or a Russian subtitle credit."""
    monkeypatch.setattr(providers_module, "trim_to_speech", lambda _samples: None)
    assert _post(client).json() == {"text": "", "language": None, "reason": "no-speech"}
    assert local.calls == 0


def test_speech_whisper_makes_nothing_of_says_so(client: TestClient, local: _LocalModel) -> None:
    local.text = "  "
    assert _post(client).json()["reason"] == "no-speech"


def test_a_recording_over_the_limit_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_module, "MAX_DICTATION_BYTES", 1000)
    assert _post(client).status_code == 413


def test_what_is_not_a_recording_is_refused(client: TestClient) -> None:
    assert client.post("/stt/churn", content=b"hello").status_code == 422


def test_the_caller_must_be_entitled(client: TestClient) -> None:
    def refuse() -> Identity:
        raise HTTPException(status_code=403)

    client.app.dependency_overrides[resolve_identity] = refuse  # type: ignore[attr-defined]
    assert _post(client).status_code == 403


class _GroqClient:
    def __init__(self, text: str) -> None:
        self.closed = False
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._create))
        self._text = text

    async def _create(self, **_kwargs: object) -> object:  # ruff: ignore[unused-async]
        return SimpleNamespace(text=self._text, language="spanish")

    async def close(self) -> None:  # ruff: ignore[unused-async]
        self.closed = True


def test_groq_transcribes_when_it_is_the_active_provider(
    client: TestClient, local: _LocalModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    groq = _GroqClient(" ¿Qué ves? ")
    build = providers_module.build_stt_service

    def build_with_fake_client(config: Any, *, provider: str | None = None) -> Any:
        service: Any = build(config, provider=provider)
        service._client = groq
        return service

    monkeypatch.setattr(providers_module, "build_stt_service", build_with_fake_client)
    monkeypatch.setattr(stt_module, "resolve_active_providers", lambda _config: ("groq", "piper"))

    assert _post(client).json() == {"text": "¿Qué ves?", "language": "es", "reason": None}
    assert groq.closed and local.calls == 0


def test_deepgram_dictation_goes_to_local_whisper(
    client: TestClient, local: _LocalModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never to another vendor than the one picked: Deepgram is a live streaming service only here."""
    monkeypatch.setattr(stt_module, "resolve_active_providers", lambda _config: ("deepgram", "piper"))
    assert _post(client).json()["text"] == "Hola desde aquí"
    assert local.calls == 1
