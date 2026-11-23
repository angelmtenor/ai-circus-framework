"""
- Title:    STT/TTS provider factory
- Author:   Angel Martinez-Tenor

Mirrors how llm-gateway/litellm_config.yaml treats LLM providers as swappable
config rather than a hardcoded SDK call: `STT_PROVIDER`/`TTS_PROVIDER` pick a
self-hostable open-source default (faster-whisper, Piper — no API key, downloads
model weights to a local cache on first use) or a cloud/frontier provider (Groq's hosted
Whisper, Deepgram, ElevenLabs, Cartesia), used identically by the live WebSocket pipeline
(`api/ws.py`) and the one-shot `/stt` (dictation) and `/tts` endpoints (`api/stt.py`, `api/tts.py`). Groq needs no
extra: pipecat's own dependencies include the OpenAI-compatible client it uses.

The cloud providers' SDKs are deliberately NOT in this service's default dependency
set (`pipecat-ai[whisper,piper,websocket]` — see pyproject.toml) since most
deployments never opt into them; each cloud branch below imports its pipecat service
lazily so the module still loads without e.g. the `deepgram` package installed, and
raises a clear error only if an operator actually selects that provider without
having `uv add`-ed the matching pipecat-ai extra (`[deepgram]`/`[elevenlabs]`/`[cartesia]`).
"""

from __future__ import annotations

import asyncio
import math
import re
import threading
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pipecat.services.piper.tts as _piper_tts_module
import pipecat.services.whisper.stt as _whisper_stt_module
from ai_circus_shared.entitlements import PlatformRegistryClient
from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.pipeline.service_switcher import ServiceSwitcher, ServiceSwitcherStrategyManual
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.services.groq.stt import GroqSTTService
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.stt_service import STTService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

from agui_voice.core.push_to_talk import PUSH_TO_TALK_MESSAGE, SILENT_PEAK_DBFS
from agui_voice.core.speech import SAMPLE_RATE, pcm_samples, peak_level, samples_wav, trim_to_speech, wav_pcm
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


WHISPER_NO_SPEECH_PROB = 0.6
"""A Whisper segment at or above this no-speech probability is dropped. Pipecat's default, 0.4, dropped quiet speech:
a clear English question at -36 dBFS scored 0.41 and came out as nothing. 0.6 is faster-whisper's own threshold."""

WHISPER_COMPUTE_TYPE = "int8"
"""int8 weights: about half the memory and twice the speed of the float32 that "default" falls back to on a CPU."""


def describe_transcription(audio: Any, segments: list[Any], info: Any, seconds_taken: float) -> str:
    """One log line of numbers for a transcribed turn: audio length and level, the detected language, each segment's
    no-speech probability and how many characters survive `WHISPER_NO_SPEECH_PROB`. Never the words.
    """
    samples = len(audio)
    peak = float(max(abs(float(audio.max())), abs(float(audio.min())))) if samples else 0.0
    kept = sum(len(s.text.strip()) for s in segments if s.no_speech_prob < WHISPER_NO_SPEECH_PROB)
    total = sum(len(s.text.strip()) for s in segments)
    probabilities = ", ".join(f"{s.no_speech_prob:.2f}" for s in segments) or "none"
    level = f"peak {20 * math.log10(peak):.0f} dBFS" if peak > 0 else "silent"
    return (
        f"{samples / 16000:.1f} s ({level}), language {info.language} ({info.language_probability:.2f}), "
        f"no-speech [{probabilities}], {kept} of {total} character(s) kept, in {seconds_taken:.1f} s"
    )


class _MeasuredWhisperModel:
    """The cached faster-whisper model, logging one line per transcribed turn (`describe_transcription`).

    Pipecat's WhisperSTTService drops every segment at or above its no-speech threshold and then says nothing, so a
    turn that "transcribed nothing" left no trace. This tells silence, a too-quiet mic and a dropped segment apart.
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, Any]:
        """Transcribe as the real model does (in pipecat's worker thread), then log the numbers."""
        started = time.monotonic()
        segments, info = self._model.transcribe(audio, **kwargs)
        segments = list(segments)
        logger.info("whisper turn: {}", describe_transcription(audio, segments, info, time.monotonic() - started))
        return iter(segments), info

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)


def _cached_whisper_model(model_name: str, device: str, compute_type: str) -> Any:
    key = (model_name, device, compute_type)
    with _model_cache_lock:
        if key not in _whisper_model_cache:
            real = _RealWhisperModel(model_name, device=device, compute_type=compute_type)
            _whisper_model_cache[key] = _MeasuredWhisperModel(real)
        return _whisper_model_cache[key]


GROQ_STT_MODEL = "whisper-large-v3-turbo"
"""Groq's hosted Whisper: large-v3-turbo, which takes 10-16 s a sentence on this project's CPUs and well under 1 s
there."""

GROQ_TIMEOUT_SECONDS = 6.0
"""The longest a turn waits for Groq, retries included, before local Whisper takes it: the client's own default (600 s a
try, two retries, longer still on a rate limit's Retry-After) would leave the user without an answer."""

_WHISPER_LANGUAGE_NAMES = {"spanish": "es", "english": "en"}


def _groq_key(config: EnvConfig) -> str | None:
    """Groq's API key, or None when unset or empty (`.env.example` and docker-compose pass an unused key as "")."""
    key = config.GROQ_API_KEY.get_secret_value().strip() if config.GROQ_API_KEY is not None else ""
    return key or None


def whisper_language_code(language: str | None) -> str | None:
    """A Whisper API's detected language as a code: its `verbose_json` names it ("spanish") or gives the code."""
    if not language:
        return None
    value = language.strip().lower()
    return _WHISPER_LANGUAGE_NAMES.get(value, value if len(value) == 2 else None)


def _language(code: str | None, text: str) -> Language:
    """The `Language` a transcript is in, from the STT's code or (failing that) from its words, so the TTS switch
    (`agent_bridge.py`) answers a Spanish question with the Spanish voice.
    """
    try:
        return Language(code) if code else Language(guess_text_language(text))
    except ValueError:
        return Language(guess_text_language(text))


def _no_speech_notice() -> Frame:
    """Tells the browser there were no words in the turn: it would otherwise wait for an answer that will not come."""
    return RTVIServerMessageFrame(data={"type": PUSH_TO_TALK_MESSAGE, "talking": False, "reason": "no-speech"})


def _too_quiet(samples: np.ndarray) -> bool:
    return peak_level(samples) < 10 ** (SILENT_PEAK_DBFS / 20)


def _transcribe_locally(model: Any, samples: np.ndarray) -> tuple[str, str | None]:
    """What the cached local Whisper hears, and the language code it detects."""
    segments, info = model.transcribe(samples, language=None)
    kept = (s.text.strip() for s in segments if s.no_speech_prob < WHISPER_NO_SPEECH_PROB)
    return " ".join(kept).strip(), info.language


class _TurnSTTMixin:
    """Transcribes one turn only if it holds speech (core/speech.py), and says which language it was in, so the TTS
    switch (`agent_bridge.py`) answers a Spanish question with the Spanish voice.
    """

    async def hear(self, speech: np.ndarray) -> tuple[str, str | None]:
        """The words in a recording that holds speech, and their language code."""
        raise NotImplementedError

    async def hear_turn(self, samples: np.ndarray) -> AsyncGenerator[Frame]:
        """One turn's transcript, or the notice that there was none."""
        speech = None if _too_quiet(samples) else await asyncio.to_thread(trim_to_speech, samples)
        text, code = "", None
        if speech is not None:
            await self.start_processing_metrics()  # type: ignore[attr-defined]
            text, code = await self.hear(speech)
            await self.stop_processing_metrics()  # type: ignore[attr-defined]
        else:
            logger.info("stt turn: no speech in it, not transcribed")
        if text:
            user_id = self._user_id  # type: ignore[attr-defined]
            yield TranscriptionFrame(text, user_id, time_now_iso8601(), _language(code, text))
        elif not _too_quiet(samples):  # a silent turn is already told (core/push_to_talk.py)
            yield _no_speech_notice()


class LocalWhisperSTTService(_TurnSTTMixin, WhisperSTTService):
    """Self-hosted Whisper: pipecat's, with the speech check above and the language Whisper detects (pipecat's own
    transcript carries the fixed setting, none here, so a Spanish question was answered in the English voice).
    """

    async def hear(self, speech: np.ndarray) -> tuple[str, str | None]:
        """Transcribe with the cached model, off the event loop."""
        return await asyncio.to_thread(_transcribe_locally, self._model, speech)

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame]:
        """Transcribe one turn (raw PCM)."""
        async for frame in self.hear_turn(pcm_samples(audio)):
            yield frame


class GroqWhisperSTTService(_TurnSTTMixin, GroqSTTService):
    """Groq's hosted Whisper large-v3-turbo, with the spoken language detected rather than fixed, and local Whisper
    for any turn Groq cannot transcribe (no network, a bad key, over the free tier's limits, no answer within
    `GROQ_TIMEOUT_SECONDS`).

    `GroqSTTService` always sends a language (English by default), so a Spanish question was transcribed as if it
    were English; here none is sent and `verbose_json` says which one Groq heard. Each turn logs one line of numbers
    (audio length, language, characters, who transcribed it, how long it took), never the words. The audio does leave
    the cluster: that is the trade for accuracy and speed (docs/adr/0033).
    """

    def __init__(self, *, api_key: str, local_model: Callable[[], Any], **kwargs: Any) -> None:
        """`local_model` returns the cached local Whisper model, used only for a turn Groq fails."""
        super().__init__(api_key=api_key, settings=GroqSTTService.Settings(model=GROQ_STT_MODEL), **kwargs)
        self._local_model = local_model

    async def _transcribe(self, audio: bytes) -> Any:
        return await self._client.audio.transcriptions.create(
            file=("audio.wav", audio, "audio/wav"), model=self._settings.model, response_format="verbose_json"
        )

    async def hear(self, speech: np.ndarray) -> tuple[str, str | None]:
        """Transcribe with Groq, or locally if Groq fails."""
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(self._transcribe(samples_wav(speech)), GROQ_TIMEOUT_SECONDS)
            text, code, by = response.text.strip(), whisper_language_code(getattr(response, "language", None)), "groq"
        except Exception as exc:
            logger.warning("groq stt failed ({}): this turn goes to local Whisper", type(exc).__name__)
            text, code = await asyncio.to_thread(lambda: _transcribe_locally(self._local_model(), speech))
            by = "local whisper"
        logger.info(
            "groq stt turn: {:.1f} s of speech, language {}, {} character(s), by {} in {:.1f} s",
            len(speech) / SAMPLE_RATE,
            code,
            len(text),
            by,
            time.monotonic() - started,
        )
        return text, code

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame]:
        """Transcribe one turn (a WAV segment)."""
        async for frame in self.hear_turn(pcm_samples(wav_pcm(audio))):
            yield frame


@dataclass(frozen=True)
class Heard:
    """What a recording says; `reason` (`silent` or `no-speech`) tells why there is no text."""

    text: str = ""
    language: str | None = None
    reason: str | None = None


async def transcribe_recording(config: EnvConfig, pcm: bytes, *, provider: str) -> Heard:
    """Transcribe one dictated recording (16-bit mono PCM at 16 kHz) with the active provider (`api/stt.py`).

    Deepgram here is a live streaming service only, so with it picked a dictation goes to local Whisper rather than to
    another vendor than the one chosen.
    """
    samples = pcm_samples(pcm)
    if _too_quiet(samples):
        logger.info("dictation: {:.1f} s, silent, not transcribed", len(samples) / SAMPLE_RATE)
        return Heard(reason="silent")
    speech = await asyncio.to_thread(trim_to_speech, samples)
    if speech is None:
        logger.info("dictation: {:.1f} s, no speech in it, not transcribed", len(samples) / SAMPLE_RATE)
        return Heard(reason="no-speech")
    if provider == "groq":
        groq = build_stt_service(config, provider="groq")
        assert isinstance(groq, GroqWhisperSTTService)
        try:
            text, code = await groq.hear(speech)
        finally:
            await groq._client.close()
    else:
        text, code = await asyncio.to_thread(
            lambda: _transcribe_locally(
                _cached_whisper_model(config.WHISPER_MODEL, "auto", WHISPER_COMPUTE_TYPE), speech
            )
        )
    logger.info("dictation: {:.1f} s, language {}, {} character(s)", len(samples) / SAMPLE_RATE, code, len(text))
    return Heard(text=text, language=code) if text else Heard(reason="no-speech")


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
            return LocalWhisperSTTService(
                compute_type=WHISPER_COMPUTE_TYPE,
                settings=WhisperSTTService.Settings(
                    model=config.WHISPER_MODEL, language=None, no_speech_prob=WHISPER_NO_SPEECH_PROB
                ),
            )
        case "deepgram":
            if config.DEEPGRAM_API_KEY is None:
                raise ValueError("DEEPGRAM_API_KEY must be set when STT_PROVIDER=deepgram.")
            from pipecat.services.deepgram.stt import DeepgramSTTService

            return DeepgramSTTService(api_key=config.DEEPGRAM_API_KEY.get_secret_value())
        case "groq":
            groq_key = _groq_key(config)
            if groq_key is None:
                raise ValueError("GROQ_API_KEY must be set when STT_PROVIDER=groq.")
            return GroqWhisperSTTService(
                api_key=groq_key,
                # The same cached model local Whisper uses (pre-warmed at start-up), only for a turn Groq fails.
                local_model=lambda: _cached_whisper_model(config.WHISPER_MODEL, "auto", WHISPER_COMPUTE_TYPE),
            )
        case other:
            raise ValueError(f"Unknown STT_PROVIDER {other!r}: expected 'whisper', 'groq' or 'deepgram'.")


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


# Where Piper's ONNX voices are downloaded to and loaded from. PiperTTSService defaults to
# the working directory — the root-owned, read-only code dir in the image — so every voice
# download failed with PermissionError (and voice-mode TTS with it). ~/.cache is the
# persistent voice-model-cache volume (k8s/base/agui-voice.yaml, docker-compose.yml): each
# voice is downloaded once per cluster and then only loaded.
PIPER_VOICES_DIR = Path.home() / ".cache" / "pipecat" / "piper"


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
            PIPER_VOICES_DIR.mkdir(parents=True, exist_ok=True)
            services_by_language = {
                language: PiperTTSService(voice_id=voice_id, download_dir=PIPER_VOICES_DIR)
                for language, voice_id in voice_by_language.items()
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
            id="groq",
            label="Groq Whisper large-v3-turbo (cloud)",
            available=_groq_key(config) is not None,
            reason=None if _groq_key(config) is not None else "GROQ_API_KEY not set",
        ),
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
