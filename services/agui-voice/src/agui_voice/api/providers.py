"""
- Title:    Voice provider info (Settings page picker + assistant UI's "STT/TTS" label)
- Author:   ai-circus-framework contributors

Read-only: the actual mutation (switching the admin's live choice) is a
platform-registry route (`PUT /voice-settings/active`), gated by its own admin
check — this route only reports what's available *here* (which cloud providers
have their API key configured in this instance's `.env`) and what's effectively
active right now, neither of which platform-registry can see on its own.
"""

from __future__ import annotations

from ai_circus_shared.auth import Identity
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agui_voice import get_env_config
from agui_voice.core.identity import resolve_identity
from agui_voice.core.providers import (
    VoiceProviderOption,
    list_stt_providers,
    list_tts_providers,
    resolve_active_providers,
)

router = APIRouter()


class VoiceProviderOptionOut(BaseModel):
    """One selectable provider, for a Settings-page dropdown option."""

    id: str
    label: str
    available: bool
    reason: str | None = None


class VoiceProviderGroupOut(BaseModel):
    """One role's (STT or TTS) currently-active provider plus every selectable option."""

    active: str
    options: list[VoiceProviderOptionOut]


class VoiceProvidersOut(BaseModel):
    """Response body for `GET /providers/{scenario_slug}`."""

    stt: VoiceProviderGroupOut
    tts: VoiceProviderGroupOut


def _options_out(options: list[VoiceProviderOption]) -> list[VoiceProviderOptionOut]:
    return [VoiceProviderOptionOut(id=o.id, label=o.label, available=o.available, reason=o.reason) for o in options]


@router.get("/providers/{scenario_slug}", response_model=VoiceProvidersOut)
def get_providers(identity: Identity = Depends(resolve_identity)) -> VoiceProvidersOut:
    """Which STT/TTS provider is effectively active right now, and every provider this
    build of agui-voice knows how to construct (self-hosted ones always available, a
    cloud one only if its API key is configured here) — the same effective choice
    `api/ws.py`/`api/tts.py` resolve for a live request, so a page reload never shows
    something stale relative to what an actual voice turn would use.
    """
    config = get_env_config()
    stt_provider, tts_provider = resolve_active_providers(config)
    return VoiceProvidersOut(
        stt=VoiceProviderGroupOut(active=stt_provider, options=_options_out(list_stt_providers(config))),
        tts=VoiceProviderGroupOut(active=tts_provider, options=_options_out(list_tts_providers(config))),
    )
