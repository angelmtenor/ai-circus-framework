"""
- Title:    STT/TTS provider factory
- Author:   ai-circus-framework contributors

Mirrors how llm-gateway/litellm_config.yaml treats LLM providers as swappable
config rather than a hardcoded SDK call: `STT_PROVIDER`/`TTS_PROVIDER` pick a
self-hostable open-source default (faster-whisper, Piper — no API key, downloads
model weights to a local cache on first use) or a cloud/frontier provider (Deepgram,
ElevenLabs, Cartesia), used identically by both the live WebSocket pipeline
(`api/ws.py`) and the one-shot `/tts` endpoint (`api/tts.py`).

The cloud providers' SDKs are deliberately NOT in this service's default dependency
set (`pipecat-ai[whisper,piper,silero,websocket]` — see pyproject.toml) since most
deployments never opt into them; each cloud branch below imports its pipecat service
lazily so the module still loads without e.g. the `deepgram` package installed, and
raises a clear error only if an operator actually selects that provider without
having `uv add`-ed the matching pipecat-ai extra (`[deepgram]`/`[elevenlabs]`/`[cartesia]`).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import pipecat.services.piper.tts as _piper_tts_module
import pipecat.services.whisper.stt as _whisper_stt_module
from ai_circus_shared.entitlements import PlatformRegistryClient
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.service_switcher import ServiceSwitcher, ServiceSwitcherStrategyManual
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.stt_service import STTService
from pipecat.services.whisper.stt import WhisperSTTService

from agui_voice.data_model import EnvConfig

# Loading model weights is real, multi-second CPU-bound work (faster-whisper's
# ctranslate2 model, Piper's ONNX voice) — cheap once cached in this process, but a
# fresh WhisperSTTService/PiperTTSService instance reloads them from scratch every
# time, since neither accepts an already-loaded model object. Under production load
# — observed directly: several overlapping WebSocket connections each racing to build
# their own pipeline — every one of those reloads runs concurrently, fighting the
# same CPU cores, and what's a ~2-3s cold load in isolation balloons to tens of
# seconds per connection. The model objects themselves are safe to share read-only
# across concurrent inference calls (each transcribe()/synthesize() call already
# runs in its own thread via asyncio.to_thread — the same pattern faster-whisper's
# own server examples rely on), so cache them process-wide, keyed by their load
# parameters, and hand every new FrameProcessor instance the same underlying model —
# only the lightweight pipecat wrapper (which does hold per-pipeline state) is
# actually rebuilt per connection.
_model_cache_lock = threading.Lock()
_whisper_model_cache: dict[tuple[str, str, str], Any] = {}
_piper_voice_cache: dict[tuple[str, bool], Any] = {}
_RealWhisperModel = _whisper_stt_module.WhisperModel
_RealPiperVoice = _piper_tts_module.PiperVoice


def _cached_whisper_model(model_name: str, device: str, compute_type: str) -> Any:
    key = (model_name, device, compute_type)
    with _model_cache_lock:
        if key not in _whisper_model_cache:
            _whisper_model_cache[key] = _RealWhisperModel(model_name, device=device, compute_type=compute_type)
        return _whisper_model_cache[key]


class _CachedPiperVoice:
    """Drop-in replacement for `piper.PiperVoice` — same `load()` call site in
    `PiperTTSService.__init__`, but returns the same already-loaded voice instead of
    reloading the ONNX model from disk every time.
    """

    @staticmethod
    def load(path: Any, use_cuda: bool = False) -> Any:
        key = (str(path), use_cuda)
        with _model_cache_lock:
            if key not in _piper_voice_cache:
                _piper_voice_cache[key] = _RealPiperVoice.load(path, use_cuda=use_cuda)
            return _piper_voice_cache[key]


# WhisperSTTService/PiperTTSService look up `WhisperModel`/`PiperVoice` as plain
# module globals at call time (not bound at import time), so rebinding them here —
# once, when this module first loads — redirects every future construction through
# the caches above without needing to subclass or fork either service.
_whisper_stt_module.WhisperModel = _cached_whisper_model  # pyrefly: ignore [bad-assignment]
_piper_tts_module.PiperVoice = _CachedPiperVoice  # pyrefly: ignore [bad-assignment]


def build_stt_service(config: EnvConfig, *, provider: str | None = None) -> STTService:
    """The STT stage for the live voice pipeline. `provider` overrides `config.STT_PROVIDER`
    when given — see `resolve_active_providers`, which callers use to get the admin
    Settings page's live choice instead of this instance's static env default.
    """
    match provider or config.STT_PROVIDER:
        case "whisper":
            # WhisperSTTService's own hardcoded default forces `language=Language.EN`
            # (see its __init__) — without this override every utterance would be
            # transcribed *as if* it were English, mangling any other language
            # instead of actually detecting it. `language=None` is faster-whisper's
            # convention for per-utterance auto-detection.
            return WhisperSTTService(settings=WhisperSTTService.Settings(model=config.WHISPER_MODEL, language=None))
        case "deepgram":
            if config.DEEPGRAM_API_KEY is None:
                raise ValueError("DEEPGRAM_API_KEY must be set when STT_PROVIDER=deepgram.")
            from pipecat.services.deepgram.stt import DeepgramSTTService

            return DeepgramSTTService(api_key=config.DEEPGRAM_API_KEY.get_secret_value())
        case other:
            raise ValueError(f"Unknown STT_PROVIDER {other!r}: expected 'whisper' or 'deepgram'.")


_SPANISH_MARKERS = frozenset("ñáéíóúü¿¡")
_SPANISH_STOPWORDS = frozenset([
    "el",
    "la",
    "los",
    "las",
    "de",
    "que",
    "y",
    "en",
    "un",
    "una",
    "es",
    "por",
    "con",
    "para",
    "no",
    "si",
    "sí",
    "como",
    "qué",
    "cómo",
    "dónde",
    "cuándo",
    "gracias",
    "hola",
    "está",
    "esto",
    "eso",
    "pero",
    "muy",
    "más",
    "también",
    "porque",
    "cuando",
    "donde",
    "te",
    "tu",
])
_ENGLISH_STOPWORDS = frozenset([
    "the",
    "is",
    "are",
    "was",
    "were",
    "and",
    "or",
    "but",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "this",
    "that",
    "hello",
    "thanks",
    "please",
    "how",
    "what",
    "where",
    "when",
    "you",
    "your",
])


def guess_text_language(text: str) -> str:
    """Best-effort "en"/"es" guess for plain text with no STT-attached language.

    Only `/tts` (the one-shot speaker-icon endpoint) needs this: the live WS
    pipeline already gets a real per-utterance detected language from Whisper (see
    `agent_bridge.py`'s `_maybe_switch_tts_language`), but `/tts` speaks arbitrary
    already-written text with no STT stage at all. Deliberately just a heuristic
    over Spanish-specific diacritics/punctuation plus a small stopword list, not a
    real language-detection library/dependency — this only ever has to pick between
    the two languages this service actually has Piper voices for (`voice_by_language`
    below), not detect language in general.
    """
    lowered = text.lower()
    if any(char in _SPANISH_MARKERS for char in lowered):
        return "es"
    words = set(re.findall(r"[a-zñáéíóúü]+", lowered))
    es_hits = len(words & _SPANISH_STOPWORDS)
    en_hits = len(words & _ENGLISH_STOPWORDS)
    return "es" if es_hits > en_hits else "en"


def build_tts_service(
    config: EnvConfig, *, provider: str | None = None
) -> tuple[FrameProcessor, Mapping[str, FrameProcessor]]:
    """The TTS stage for the live pipeline and the one-shot `/tts` endpoint. `provider`
    overrides `config.TTS_PROVIDER` when given — see `resolve_active_providers`.

    Returns `(service_for_pipeline, language_switch_map)`. `language_switch_map` is
    `{}` unless the provider actually supports switching languages mid-conversation
    without reloading anything: Piper's own `_update_settings` explicitly does NOT
    support changing `voice` live (it would mean re-downloading/loading a whole new
    ONNX model on the request path — see PiperTTSService's `_update_settings` TODO),
    so instead we preload one `PiperTTSService` per supported language up front and
    wrap them in a `ServiceSwitcher` — a single pipeline stage that instantly routes
    to whichever already-loaded service is "active", switched at runtime by pushing
    a `ManuallySwitchServiceFrame` (see `core/agent_bridge.py`, which does this based
    on the STT stage's own per-utterance detected language). Cloud providers
    (ElevenLabs/Cartesia) aren't included here: their multilingual behavior is
    controlled by which voice/model the operator configured, not something this
    service can safely reswitch on their behalf.
    """
    match provider or config.TTS_PROVIDER:
        case "piper":
            voice_by_language = {"en": config.PIPER_VOICE_ID, "es": config.PIPER_VOICE_ID_ES}
            services_by_language = {
                language: PiperTTSService(voice_id=voice_id) for language, voice_id in voice_by_language.items()
            }
            switcher = ServiceSwitcher(
                services=list(services_by_language.values()), strategy_type=ServiceSwitcherStrategyManual
            )
            return switcher, services_by_language
        case "elevenlabs":
            if config.ELEVENLABS_API_KEY is None or not config.ELEVENLABS_VOICE_ID:
                raise ValueError("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID must be set when TTS_PROVIDER=elevenlabs.")
            from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

            return (
                ElevenLabsTTSService(
                    api_key=config.ELEVENLABS_API_KEY.get_secret_value(), voice_id=config.ELEVENLABS_VOICE_ID
                ),
                {},
            )
        case "cartesia":
            if config.CARTESIA_API_KEY is None or not config.CARTESIA_VOICE_ID:
                raise ValueError("CARTESIA_API_KEY and CARTESIA_VOICE_ID must be set when TTS_PROVIDER=cartesia.")
            from pipecat.services.cartesia.tts import CartesiaTTSService

            return (
                CartesiaTTSService(
                    api_key=config.CARTESIA_API_KEY.get_secret_value(), voice_id=config.CARTESIA_VOICE_ID
                ),
                {},
            )
        case other:
            raise ValueError(f"Unknown TTS_PROVIDER {other!r}: expected 'piper', 'elevenlabs', or 'cartesia'.")


def build_vad_analyzer() -> SileroVADAnalyzer:
    """Voice-activity detection for the live pipeline's turn-taking/barge-in — always
    local regardless of the STT/TTS provider choice.
    """
    return SileroVADAnalyzer()


@dataclass(frozen=True)
class VoiceProviderOption:
    """One selectable STT/TTS provider for the Settings page's voice-mode picker and
    the assistant UI's "STT: ... / TTS: ..." label — `available` only means "this
    instance has the required secret(s) configured", not "verified working" (a
    cloud key can still be invalid/expired/quota-exceeded; there's no live "Test"
    call here, unlike llm_settings.test_provider).
    """

    id: str
    label: str
    available: bool
    reason: str | None = None
    """Why `available` is False — shown as a disabled-option hint. None when available."""


def list_stt_providers(config: EnvConfig) -> list[VoiceProviderOption]:
    """Every STT provider this build of agui-voice knows how to construct, and
    whether each is actually usable right now (self-hosted always is; a cloud one
    needs its own API key set in this instance's `.env`).
    """
    return [
        VoiceProviderOption(id="whisper", label="Whisper (self-hosted)", available=True),
        VoiceProviderOption(
            id="deepgram",
            label="Deepgram (cloud)",
            available=config.DEEPGRAM_API_KEY is not None,
            reason=None if config.DEEPGRAM_API_KEY is not None else "DEEPGRAM_API_KEY not set",
        ),
    ]


def list_tts_providers(config: EnvConfig) -> list[VoiceProviderOption]:
    """Every TTS provider this build of agui-voice knows how to construct, and
    whether each is actually usable right now.
    """
    return [
        VoiceProviderOption(id="piper", label="Piper (self-hosted)", available=True),
        VoiceProviderOption(
            id="elevenlabs",
            label="ElevenLabs (cloud)",
            available=bool(config.ELEVENLABS_API_KEY and config.ELEVENLABS_VOICE_ID),
            reason=None
            if config.ELEVENLABS_API_KEY and config.ELEVENLABS_VOICE_ID
            else "ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID not set",
        ),
        VoiceProviderOption(
            id="cartesia",
            label="Cartesia (cloud)",
            available=bool(config.CARTESIA_API_KEY and config.CARTESIA_VOICE_ID),
            reason=None
            if config.CARTESIA_API_KEY and config.CARTESIA_VOICE_ID
            else "CARTESIA_API_KEY and CARTESIA_VOICE_ID not set",
        ),
    ]


def resolve_active_providers(config: EnvConfig) -> tuple[str, str]:
    """`(stt_provider, tts_provider)` to actually use for this connection/call: the
    admin's live Settings-page choice (persisted in platform-registry), falling back
    to this instance's static `STT_PROVIDER`/`TTS_PROVIDER` if platform-registry is
    unreachable or the chosen provider isn't actually usable here (e.g. a cloud
    provider picked deployment-wide but this instance has no API key for it) — the
    shared setting is a preference, never a hard failure for a deployment that isn't
    configured to honor it.
    """
    stt_provider, tts_provider = config.STT_PROVIDER, config.TTS_PROVIDER
    registry = PlatformRegistryClient(base_url=config.PLATFORM_REGISTRY_URL)
    try:
        active_stt, active_tts = registry.get_active_voice_settings(
            admin_api_key=config.ADMIN_API_KEY.get_secret_value()
        )
    except httpx.HTTPError as exc:
        logger.warning("resolve_active_providers: platform-registry unreachable, using env defaults: {}", exc)
        return stt_provider, tts_provider

    stt_available = {option.id for option in list_stt_providers(config) if option.available}
    tts_available = {option.id for option in list_tts_providers(config) if option.available}
    if active_stt in stt_available:
        stt_provider = active_stt
    else:
        logger.warning(
            "resolve_active_providers: active stt_provider {!r} unavailable, using {!r}", active_stt, stt_provider
        )
    if active_tts in tts_available:
        tts_provider = active_tts
    else:
        logger.warning(
            "resolve_active_providers: active tts_provider {!r} unavailable, using {!r}", active_tts, tts_provider
        )
    return stt_provider, tts_provider
