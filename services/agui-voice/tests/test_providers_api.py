"""Tests for api/providers.py's GET /providers/{scenario_slug} endpoint."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from ai_circus_shared.auth import Identity
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agui_voice.api.providers as providers_api_module
from agui_voice.api.providers import router
from agui_voice.core.identity import resolve_identity
from agui_voice.core.providers import VoiceProviderOption


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    """A TestClient with identity overridden and fake provider info wired in."""
    monkeypatch.setattr(providers_api_module, "get_env_config", lambda: object())
    monkeypatch.setattr(providers_api_module, "resolve_active_providers", lambda _config: ("whisper", "piper"))
    monkeypatch.setattr(
        providers_api_module,
        "list_stt_providers",
        lambda _config: [
            VoiceProviderOption(id="whisper", label="Whisper (self-hosted)", available=True),
            VoiceProviderOption(id="deepgram", label="Deepgram (cloud)", available=False, reason="no key"),
        ],
    )
    monkeypatch.setattr(
        providers_api_module,
        "list_tts_providers",
        lambda _config: [
            VoiceProviderOption(id="piper", label="Piper (self-hosted)", available=True),
            VoiceProviderOption(id="elevenlabs", label="ElevenLabs (cloud)", available=False, reason="no key"),
        ],
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_providers_reports_active_and_options(client: TestClient) -> None:
    """The response nests each role's currently-active provider and full option list."""
    response = client.get("/providers/churn")

    assert response.status_code == 200
    body = response.json()
    assert body["stt"]["active"] == "whisper"
    assert body["tts"]["active"] == "piper"
    assert {o["id"] for o in body["stt"]["options"]} == {"whisper", "deepgram"}
    assert {o["id"] for o in body["tts"]["options"]} == {"piper", "elevenlabs"}


def test_get_providers_reports_unavailable_reason(client: TestClient) -> None:
    """An unavailable cloud provider carries its reason through to the response."""
    response = client.get("/providers/churn")

    deepgram = next(o for o in response.json()["stt"]["options"] if o["id"] == "deepgram")
    assert deepgram["available"] is False
    assert deepgram["reason"] == "no key"
