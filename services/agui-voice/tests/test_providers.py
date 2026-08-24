"""Tests for core/providers.py's STT/TTS/VAD factory functions.

The real service classes (WhisperSTTService, PiperTTSService, ...) load model
weights on construction — these tests monkeypatch each class in the providers
module's own namespace so the factory logic is verified without touching disk
or the network.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

import agui_voice.core.providers as providers_module


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
    monkeypatch.setattr(providers_module, "WhisperSTTService", _FakeWhisperSTTService(calls))

    result = providers_module.build_stt_service(_base_config())

    assert result == "whisper-service"
    assert len(calls) == 1
    assert calls[0]["settings"].model == "base"
    assert calls[0]["settings"].language is None


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


def test_build_tts_service_piper(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTS_PROVIDER=piper preloads one PiperTTSService per supported language, wraps
    them in a ServiceSwitcher (so switching voice needs no reload — see the
    docstring), and returns a language -> service switch map for AgentBridgeProcessor.
    """
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
    assert calls == [{"voice_id": "en_US-lessac-medium"}, {"voice_id": "es_ES-davefx-medium"}]
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


def test_build_vad_analyzer_returns_silero(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_vad_analyzer always returns a SileroVADAnalyzer, regardless of STT/TTS config."""
    monkeypatch.setattr(providers_module, "SileroVADAnalyzer", lambda: "silero-analyzer")

    assert providers_module.build_vad_analyzer() == "silero-analyzer"


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
