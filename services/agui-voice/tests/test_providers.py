"""Tests for core/providers.py's STT/TTS/VAD factory functions.

The real service classes (WhisperSTTService, PiperTTSService, ...) load model
weights on construction — these tests monkeypatch each class in the providers
module's own namespace so the factory logic is verified without touching disk
or the network.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

import agui_voice.core.providers as providers_module


@pytest.fixture(autouse=True)
def _tones_count_as_speech(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests transcribe tones; whether a recording holds speech is test_speech.py's business."""
    monkeypatch.setattr(providers_module, "trim_to_speech", lambda samples: samples)


@pytest.fixture(autouse=True)
def _clear_model_caches() -> None:
    """Each test starts with cold model caches — otherwise an earlier test's cached
    (fake) model could leak into a later test's assertions.
    """
    providers_module._whisper_model_cache.clear()
    providers_module._piper_voice_cache.clear()


class FakeConfig(SimpleNamespace):
    """Minimal stand-in for EnvConfig, covering the fields providers.py reads."""


def _base_config(**overrides: object) -> FakeConfig:
    defaults = {
        "STT_PROVIDER": "whisper",
        "TTS_PROVIDER": "piper",
        "WHISPER_MODEL": "base",
        "PIPER_VOICE_ID": "en_US-lessac-medium",
        "PIPER_VOICE_ID_ES": "es_ES-davefx-medium",
        "GROQ_API_KEY": None,
        "DEEPGRAM_API_KEY": None,
        "ELEVENLABS_API_KEY": None,
        "ELEVENLABS_VOICE_ID": None,
        "CARTESIA_API_KEY": None,
        "CARTESIA_VOICE_ID": None,
        "PLATFORM_REGISTRY_URL": "http://platform-registry:8000",
        "ADMIN_API_KEY": SecretStr("test-admin-key"),
    }
    defaults.update(overrides)
    return FakeConfig(**defaults)


def test_build_stt_service_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """STT_PROVIDER=whisper builds a WhisperSTTService with the configured model and
    `language=None` — auto-detect, overriding WhisperSTTService's own hardcoded
    default of forcing English (see providers.py's comment).
    """
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(providers_module, "LocalWhisperSTTService", _FakeWhisperSTTService(calls))

    result = providers_module.build_stt_service(_base_config())

    assert result == "whisper-service"
    assert len(calls) == 1
    assert calls[0]["settings"].model == "base"
    assert calls[0]["settings"].language is None
    # Quiet speech is kept (pipecat's 0.4 dropped it) and the model runs int8, not the float32 "default" gives on CPU.
    assert calls[0]["settings"].no_speech_prob is providers_module.WHISPER_NO_SPEECH_PROB
    assert pytest.approx(0.6) == providers_module.WHISPER_NO_SPEECH_PROB
    assert calls[0]["compute_type"] == "int8"


@pytest.mark.parametrize("key", [None, SecretStr("")])  # .env.example and docker-compose pass an unused key as ""
def test_build_stt_service_groq_needs_its_key(key: SecretStr | None) -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        providers_module.build_stt_service(_base_config(STT_PROVIDER="groq", GROQ_API_KEY=key))


@pytest.mark.parametrize("key", [None, SecretStr("")])
def test_groq_is_offered_only_with_its_key(key: SecretStr | None) -> None:
    without = {o.id: o for o in providers_module.list_stt_providers(_base_config(GROQ_API_KEY=key))}
    assert without["groq"].available is False and without["groq"].reason == "GROQ_API_KEY not set"
    with_key = {o.id: o for o in providers_module.list_stt_providers(_base_config(GROQ_API_KEY=SecretStr("gsk")))}
    assert with_key["groq"].available is True


def _wav(seconds: float = 0.5, amplitude: int = 3277) -> bytes:
    """A square wave at `amplitude` (3277 is -20 dBFS) as a 16-bit WAV."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        pair = amplitude.to_bytes(2, "little", signed=True) + (-amplitude).to_bytes(2, "little", signed=True)
        wav.writeframes(pair * int(8000 * seconds))
    return buffer.getvalue()


class _Segment:
    def __init__(self, text: str, no_speech_prob: float) -> None:
        self.text, self.no_speech_prob = text, no_speech_prob


class _LocalModel:
    """Stands in for the cached local Whisper model the Groq service falls back to."""

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio: object, **kwargs: object) -> tuple[object, object]:
        self.calls += 1
        return iter([_Segment(" Hola desde aquí", 0.1), _Segment(" ruido", 0.9)]), SimpleNamespace(language="es")


async def _frames(service: object, audio: bytes) -> list[object]:
    return [frame async for frame in service.run_stt(audio)]  # type: ignore[attr-defined]


async def test_groq_transcribes_with_the_detected_language_and_sends_none() -> None:
    """GroqSTTService always sent a language (English by default); now Groq detects it, and the transcript carries
    it so the Spanish Piper voice answers a Spanish question.
    """
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.transcriptions.language import Language

    sent: list[dict[str, object]] = []

    async def create(**kwargs: object) -> object:  # ruff: ignore[unused-async] (stands in for the async client call)
        sent.append(kwargs)
        return SimpleNamespace(text=" ¿Qué escenario me recomiendas? ", language="spanish")

    local = _LocalModel()
    service = providers_module.build_stt_service(_base_config(STT_PROVIDER="groq", GROQ_API_KEY=SecretStr("gsk")))
    service._local_model = lambda: local
    service._client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))

    frames = await _frames(service, _wav())

    assert sent[0]["model"] == "whisper-large-v3-turbo" and sent[0]["response_format"] == "verbose_json"
    assert "language" not in sent[0]
    assert len(frames) == 1 and isinstance(frames[0], TranscriptionFrame)
    assert frames[0].text == "¿Qué escenario me recomiendas?" and frames[0].language == Language.ES
    assert local.calls == 0


async def test_a_turn_groq_fails_is_transcribed_by_local_whisper() -> None:
    """No network, a bad key or the free tier's limit must not leave the user with nothing."""
    from pipecat.transcriptions.language import Language

    async def create(**kwargs: object) -> object:  # ruff: ignore[unused-async]
        raise RuntimeError("rate limited")

    local = _LocalModel()
    service = providers_module.build_stt_service(_base_config(STT_PROVIDER="groq", GROQ_API_KEY=SecretStr("gsk")))
    service._local_model = lambda: local
    service._client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))

    frames = await _frames(service, _wav())

    assert local.calls == 1
    assert [(f.text, f.language) for f in frames] == [("Hola desde aquí", Language.ES)]  # the noise segment dropped


@pytest.mark.parametrize(("amplitude", "sent"), [(0, False), (8, False), (520, True)])  # silent, -72, -36 dBFS
async def test_a_silent_turn_is_never_sent_to_groq(amplitude: int, sent: bool) -> None:
    """Hosted Whisper transcribes a muted microphone as "Thank you.", which reached the agent as a question; quiet
    speech (-36 dBFS) still goes.
    """
    calls: list[object] = []

    async def create(**kwargs: object) -> object:  # ruff: ignore[unused-async]
        calls.append(kwargs)
        return SimpleNamespace(text="Thank you.", language="english")

    service = providers_module.build_stt_service(_base_config(STT_PROVIDER="groq", GROQ_API_KEY=SecretStr("gsk")))
    service._local_model = lambda: pytest.fail("a silent turn needs no transcription at all")
    service._client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))

    frames = await _frames(service, _wav(amplitude=amplitude))

    assert bool(calls) is sent and bool(frames) is sent


async def test_a_turn_without_words_is_not_transcribed_and_is_told(monkeypatch: pytest.MonkeyPatch) -> None:
    """A click or a breath: hosted Whisper answered "Thank you." or a Russian subtitle credit, as the user's words."""
    from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

    monkeypatch.setattr(providers_module, "trim_to_speech", lambda _samples: None)
    service = providers_module.build_stt_service(_base_config(STT_PROVIDER="groq", GROQ_API_KEY=SecretStr("gsk")))
    service._local_model = lambda: pytest.fail("nothing to transcribe")
    service._client = SimpleNamespace()  # calling it would fail

    frames = await _frames(service, _wav())

    assert len(frames) == 1 and isinstance(frames[0], RTVIServerMessageFrame)
    assert frames[0].data == {"type": "push-to-talk", "talking": False, "reason": "no-speech"}


async def test_local_whisper_transcribes_with_the_detected_language() -> None:
    """Pipecat's own transcript carried none, so a Spanish question was answered in the English voice."""
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.transcriptions.language import Language

    local = _LocalModel()
    providers_module._whisper_model_cache["base", "auto", "int8"] = local
    service = providers_module.build_stt_service(_base_config())

    frames = await _frames(service, b"\x00\x20" * 8000)  # raw PCM, as pipecat hands it to a local model

    assert len(frames) == 1 and isinstance(frames[0], TranscriptionFrame)
    assert (frames[0].text, frames[0].language) == ("Hola desde aquí", Language.ES)


async def test_speech_that_comes_out_empty_is_told() -> None:
    from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

    async def create(**kwargs: object) -> object:  # ruff: ignore[unused-async]
        return SimpleNamespace(text=" ", language="english")

    service = providers_module.build_stt_service(_base_config(STT_PROVIDER="groq", GROQ_API_KEY=SecretStr("gsk")))
    service._client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))

    frames = await _frames(service, _wav())

    assert [type(f) for f in frames] == [RTVIServerMessageFrame]


async def test_a_turn_groq_never_answers_goes_to_local_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client would wait 10 minutes and retry; the turn waits `GROQ_TIMEOUT_SECONDS`, then local Whisper has it."""
    import asyncio

    async def create(**kwargs: object) -> object:
        await asyncio.sleep(60)
        raise AssertionError("not reached")

    monkeypatch.setattr(providers_module, "GROQ_TIMEOUT_SECONDS", 0.01)
    local = _LocalModel()
    service = providers_module.build_stt_service(_base_config(STT_PROVIDER="groq", GROQ_API_KEY=SecretStr("gsk")))
    service._local_model = lambda: local
    service._client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))

    frames = await asyncio.wait_for(_frames(service, _wav()), timeout=5)

    assert [f.text for f in frames] == ["Hola desde aquí"] and local.calls == 1


@pytest.mark.parametrize(
    ("given", "expected"), [("spanish", "es"), ("English", "en"), ("es", "es"), (None, None), ("klingon", None)]
)
def test_whisper_language_code(given: str | None, expected: str | None) -> None:
    assert providers_module.whisper_language_code(given) == expected


class _FakeWhisperSTTService:
    """Stands in for `WhisperSTTService`, whose real `Settings` attribute is needed
    to build a genuine `Settings` instance (providers.py calls
    `WhisperSTTService.Settings(...)`) while the class itself is faked out.
    """

    def __init__(self, calls: list[dict[str, object]]) -> None:
        self._calls = calls
        from pipecat.services.whisper.stt import WhisperSTTService as RealWhisperSTTService

        self.Settings = RealWhisperSTTService.Settings

    def __call__(self, **kwargs: object) -> str:
        self._calls.append(kwargs)
        return "whisper-service"


def test_build_stt_service_deepgram(monkeypatch: pytest.MonkeyPatch) -> None:
    """STT_PROVIDER=deepgram builds a DeepgramSTTService with the unwrapped API key.

    DeepgramSTTService is imported lazily inside build_stt_service (its SDK isn't in
    this service's default dependency set — see providers.py's module docstring), so
    the fake is patched onto pipecat's own module rather than onto providers_module.
    """
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pipecat.services.deepgram.stt.DeepgramSTTService", lambda **kw: calls.append(kw) or "deepgram-service"
    )

    config = _base_config(STT_PROVIDER="deepgram", DEEPGRAM_API_KEY=SecretStr("dg-key"))
    result = providers_module.build_stt_service(config)

    assert result == "deepgram-service"
    assert calls == [{"api_key": "dg-key"}]


def test_build_stt_service_deepgram_without_key_raises() -> None:
    """STT_PROVIDER=deepgram with no DEEPGRAM_API_KEY fails loudly rather than silently."""
    with pytest.raises(ValueError, match="DEEPGRAM_API_KEY"):
        providers_module.build_stt_service(_base_config(STT_PROVIDER="deepgram"))


def test_build_stt_service_unknown_provider_raises() -> None:
    """An unrecognized STT_PROVIDER value fails loudly."""
    with pytest.raises(ValueError, match="Unknown STT_PROVIDER"):
        providers_module.build_stt_service(_base_config(STT_PROVIDER="not-a-provider"))


def test_build_tts_service_piper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """TTS_PROVIDER=piper preloads one PiperTTSService per supported language, wraps
    them in a ServiceSwitcher (so switching voice needs no reload — see the
    docstring), and returns a language -> service switch map for AgentBridgeProcessor.
    Voices go to the writable cache volume, never the (read-only) working directory.
    """
    voices_dir = tmp_path / "cache" / "pipecat" / "piper"
    monkeypatch.setattr(providers_module, "PIPER_VOICES_DIR", voices_dir)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(providers_module, "PiperTTSService", lambda **kw: calls.append(kw) or f"piper-{kw['voice_id']}")
    switcher_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        providers_module,
        "ServiceSwitcher",
        lambda **kw: switcher_calls.append(kw) or "the-switcher",
    )

    result, switch_map = providers_module.build_tts_service(_base_config())

    assert result == "the-switcher"
    assert calls == [
        {"voice_id": "en_US-lessac-medium", "download_dir": voices_dir},
        {"voice_id": "es_ES-davefx-medium", "download_dir": voices_dir},
    ]
    assert voices_dir.is_dir()  # created up front — Piper's downloader doesn't create parents
    assert switcher_calls == [
        {
            "services": ["piper-en_US-lessac-medium", "piper-es_ES-davefx-medium"],
            "strategy_type": providers_module.ServiceSwitcherStrategyManual,
        }
    ]
    assert switch_map == {"en": "piper-en_US-lessac-medium", "es": "piper-es_ES-davefx-medium"}


def test_build_tts_service_elevenlabs(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTS_PROVIDER=elevenlabs builds an ElevenLabsTTSService with the unwrapped API key.

    ElevenLabsTTSService is imported lazily inside build_tts_service, so the fake is
    patched onto pipecat's own module rather than onto providers_module.
    """
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pipecat.services.elevenlabs.tts.ElevenLabsTTSService",
        lambda **kw: calls.append(kw) or "elevenlabs-service",
    )

    config = _base_config(
        TTS_PROVIDER="elevenlabs", ELEVENLABS_API_KEY=SecretStr("el-key"), ELEVENLABS_VOICE_ID="voice-1"
    )
    result, switch_map = providers_module.build_tts_service(config)

    assert result == "elevenlabs-service"
    assert calls == [{"api_key": "el-key", "voice_id": "voice-1"}]
    assert switch_map == {}


def test_build_tts_service_elevenlabs_without_voice_raises() -> None:
    """TTS_PROVIDER=elevenlabs with no ELEVENLABS_VOICE_ID fails loudly."""
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID"):
        providers_module.build_tts_service(_base_config(TTS_PROVIDER="elevenlabs", ELEVENLABS_API_KEY=SecretStr("k")))


def test_build_tts_service_cartesia(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTS_PROVIDER=cartesia builds a CartesiaTTSService with the unwrapped API key.

    CartesiaTTSService is imported lazily inside build_tts_service, so the fake is
    patched onto pipecat's own module rather than onto providers_module.
    """
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "pipecat.services.cartesia.tts.CartesiaTTSService", lambda **kw: calls.append(kw) or "cartesia-service"
    )

    config = _base_config(TTS_PROVIDER="cartesia", CARTESIA_API_KEY=SecretStr("ct-key"), CARTESIA_VOICE_ID="voice-2")
    result, switch_map = providers_module.build_tts_service(config)

    assert result == "cartesia-service"
    assert calls == [{"api_key": "ct-key", "voice_id": "voice-2"}]
    assert switch_map == {}


def test_build_tts_service_unknown_provider_raises() -> None:
    """An unrecognized TTS_PROVIDER value fails loudly."""
    with pytest.raises(ValueError, match="Unknown TTS_PROVIDER"):
        providers_module.build_tts_service(_base_config(TTS_PROVIDER="not-a-provider"))


@pytest.mark.parametrize(
    "text",
    [
        "¿Cómo estás? Muchas gracias por tu ayuda",
        "Hola, esto es una prueba de la aplicación",
        "El modelo no está disponible en este momento",
    ],
)
def test_guess_text_language_detects_spanish(text: str) -> None:
    """Spanish-specific diacritics/punctuation and stopwords are enough to tell
    Spanish text apart from English without a real language-detection dependency.
    """
    assert providers_module.guess_text_language(text) == "es"


@pytest.mark.parametrize(
    "text",
    [
        "Hello, thanks for your help with this",
        "The model is not available right now",
        "Please try again when you have a moment",
    ],
)
def test_guess_text_language_detects_english(text: str) -> None:
    """English text with no Spanish markers/stopwords falls back to English."""
    assert providers_module.guess_text_language(text) == "en"


def test_list_stt_providers_self_hosted_always_available() -> None:
    """Whisper is always usable regardless of which cloud keys are (or aren't) set."""
    options = providers_module.list_stt_providers(_base_config())
    whisper = next(o for o in options if o.id == "whisper")
    assert whisper.available is True
    assert whisper.reason is None


def test_list_stt_providers_deepgram_unavailable_without_key() -> None:
    """A cloud provider with no configured API key is reported unavailable, with a reason."""
    options = providers_module.list_stt_providers(_base_config(DEEPGRAM_API_KEY=None))
    deepgram = next(o for o in options if o.id == "deepgram")
    assert deepgram.available is False
    assert deepgram.reason is not None


def test_list_stt_providers_deepgram_available_with_key() -> None:
    """A cloud provider becomes available once its API key is configured."""
    options = providers_module.list_stt_providers(_base_config(DEEPGRAM_API_KEY=SecretStr("dg-key")))
    deepgram = next(o for o in options if o.id == "deepgram")
    assert deepgram.available is True
    assert deepgram.reason is None


def test_list_tts_providers_elevenlabs_requires_both_key_and_voice_id() -> None:
    """ElevenLabs needs BOTH the API key and a voice id — either alone isn't enough."""
    key_only = providers_module.list_tts_providers(
        _base_config(ELEVENLABS_API_KEY=SecretStr("el-key"), ELEVENLABS_VOICE_ID=None)
    )
    assert next(o for o in key_only if o.id == "elevenlabs").available is False

    both = providers_module.list_tts_providers(
        _base_config(ELEVENLABS_API_KEY=SecretStr("el-key"), ELEVENLABS_VOICE_ID="voice-1")
    )
    assert next(o for o in both if o.id == "elevenlabs").available is True


class _FakeRegistryClient:
    """Stand-in for PlatformRegistryClient, covering only get_active_voice_settings."""

    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url

    def get_active_voice_settings(self, *, admin_api_key: str) -> tuple[str, str]:
        return _FakeRegistryClient.result


def test_resolve_active_providers_uses_platform_registrys_choice_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admin's live Settings-page choice wins when it's actually usable here."""
    _FakeRegistryClient.result = ("deepgram", "elevenlabs")
    monkeypatch.setattr(providers_module, "PlatformRegistryClient", _FakeRegistryClient)
    config = _base_config(
        DEEPGRAM_API_KEY=SecretStr("dg-key"),
        ELEVENLABS_API_KEY=SecretStr("el-key"),
        ELEVENLABS_VOICE_ID="voice-1",
    )

    assert providers_module.resolve_active_providers(config) == ("deepgram", "elevenlabs")


def test_resolve_active_providers_falls_back_when_chosen_provider_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared choice this instance can't actually honor (missing API key) falls
    back to its own static env default instead of crashing.
    """
    _FakeRegistryClient.result = ("deepgram", "elevenlabs")
    monkeypatch.setattr(providers_module, "PlatformRegistryClient", _FakeRegistryClient)
    config = _base_config()  # no cloud keys configured

    assert providers_module.resolve_active_providers(config) == ("whisper", "piper")


def test_resolve_active_providers_falls_back_when_platform_registry_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network failure talking to platform-registry falls back to the static env
    default rather than breaking every voice request.
    """

    class _RaisingClient:
        def __init__(self, *, base_url: str) -> None:
            pass

        def get_active_voice_settings(self, *, admin_api_key: str) -> tuple[str, str]:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(providers_module, "PlatformRegistryClient", _RaisingClient)
    config = _base_config(STT_PROVIDER="whisper", TTS_PROVIDER="piper")

    assert providers_module.resolve_active_providers(config) == ("whisper", "piper")


def test_cached_whisper_model_loads_once_for_the_same_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two connections requesting the same (model, device, compute_type) share the
    same loaded model object instead of each paying the multi-second load cost —
    the whole point of caching (see providers.py's module docstring for why: under
    real load, concurrent reloads were observed to compound into tens of seconds).
    """
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(providers_module, "_RealWhisperModel", lambda *a, **kw: calls.append(a) or object())

    first = providers_module._cached_whisper_model("base", "auto", "default")
    second = providers_module._cached_whisper_model("base", "auto", "default")

    assert first is second
    assert len(calls) == 1


def test_the_cached_whisper_model_logs_each_turn_in_numbers_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """A turn that "transcribed nothing" leaves a line telling silence, a quiet mic and a dropped segment apart,
    without the words; the segments reach pipecat unchanged.
    """
    import numpy as np

    class Segment:
        def __init__(self, text: str, no_speech_prob: float) -> None:
            self.text, self.no_speech_prob = text, no_speech_prob

    class Info:
        language, language_probability = "es", 0.93

    segments = [Segment(" Hola, ¿qué tal?", 0.05), Segment(" ruido", 0.75)]

    class RealModel:
        def transcribe(self, audio: object, **kwargs: object) -> tuple[object, object]:
            return (s for s in segments), Info()

    monkeypatch.setattr(providers_module, "_RealWhisperModel", lambda *a, **kw: RealModel())
    lines: list[str] = []
    sink = providers_module.logger.add(lambda message: lines.append(message.record["message"]), level="INFO")
    try:
        model = providers_module._cached_whisper_model("base-logging", "cpu", "int8")
        audio = np.full(32000, 0.1, dtype=np.float32)  # 2 s at 16 kHz, -20 dBFS
        got, info = model.transcribe(audio, language=None)
    finally:
        providers_module.logger.remove(sink)

    assert list(got) == segments and info.language == "es"
    assert len(lines) == 1
    assert lines[0].startswith(
        "whisper turn: 2.0 s (peak -20 dBFS), language es (0.93), no-speech [0.05, 0.75], "
        "15 of 20 character(s) kept, in "
    )
    assert "Hola" not in lines[0] and "ruido" not in lines[0]


def test_cached_whisper_model_loads_separately_per_param_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A different model/device/compute_type is a cache miss, not accidentally shared."""
    monkeypatch.setattr(providers_module, "_RealWhisperModel", lambda *a, **kw: object())

    base_model = providers_module._cached_whisper_model("base", "auto", "default")
    tiny_model = providers_module._cached_whisper_model("tiny", "auto", "default")

    assert base_model is not tiny_model


def test_cached_piper_voice_loads_once_for_the_same_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two PiperTTSService constructions for the same voice share the same loaded
    ONNX voice instead of re-reading it from disk every time.
    """
    calls: list[object] = []

    class FakeRealPiperVoice:
        @staticmethod
        def load(path: object, use_cuda: bool = False) -> object:
            calls.append(path)
            return object()

    monkeypatch.setattr(providers_module, "_RealPiperVoice", FakeRealPiperVoice)

    first = providers_module._CachedPiperVoice.load("en_US-lessac-medium.onnx")
    second = providers_module._CachedPiperVoice.load("en_US-lessac-medium.onnx")

    assert first is second
    assert len(calls) == 1


def test_piper_voices_live_on_the_home_cache_volume() -> None:
    """The k8s/compose voice-model-cache volume is mounted at ~/.cache — the voices dir must be under it."""
    assert providers_module.PIPER_VOICES_DIR.is_relative_to(Path.home() / ".cache")
