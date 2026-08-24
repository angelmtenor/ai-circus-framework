"""
test_env_profiles.py
--------------------

Tests for the environment-aware configuration loading.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import pytest

from agui_voice.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache and set the mandatory fields with no profile default."""
    get_env_config.cache_clear()
    # AUTH_DISABLED/ADMIN_API_KEY/CORS_ALLOWED_ORIGINS/STT_PROVIDER/TTS_PROVIDER/
    # WHISPER_MODEL/PIPER_VOICE_ID/PIPER_VOICE_ID_ES intentionally have no
    # settings.yaml default (see settings.yaml) — they must come from real env vars.
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://react.localhost")
    monkeypatch.setenv("STT_PROVIDER", "whisper")
    monkeypatch.setenv("TTS_PROVIDER", "piper")
    monkeypatch.setenv("WHISPER_MODEL", "base")
    monkeypatch.setenv("PIPER_VOICE_ID", "en_US-lessac-medium")
    monkeypatch.setenv("PIPER_VOICE_ID_ES", "es_ES-davefx-medium")


def test_get_env_config_default_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no APP_ENVIRONMENT set, the 'local' profile is used."""
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.ASSISTANT_SERVICE_URL == "http://assistant.localhost"
    assert config.PLATFORM_REGISTRY_URL == "http://localhost:8010"


def test_get_env_config_docker_profile() -> None:
    """The 'docker' profile points at in-network hostnames."""
    config = get_env_config(env="docker")

    assert config.ASSISTANT_SERVICE_URL == "http://assistant:8000"
    assert config.RAG_AGENT_SERVICE_URL == "http://rag-agent:8000"
    assert config.FORM_AGENT_SERVICE_URL == "http://form-agent:8000"
    assert config.PLATFORM_REGISTRY_URL == "http://platform-registry:8000"


@pytest.mark.parametrize("profile", ["local", "docker"])
def test_get_env_config_reads_app_environment(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """APP_ENVIRONMENT selects the active profile; base defaults always apply.

    `staging`/`production` are intentionally left as empty placeholder profiles in
    settings.yaml (nothing is deployed there yet), so they're not covered here.
    """
    monkeypatch.setenv("APP_ENVIRONMENT", profile)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"


def test_get_env_config_explicit_env_overrides_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit `env=` argument takes priority over the APP_ENVIRONMENT variable."""
    monkeypatch.setenv("APP_ENVIRONMENT", "docker")

    config = get_env_config(env="local")

    assert config.ASSISTANT_SERVICE_URL == "http://assistant.localhost"


def test_admin_api_key_has_no_yaml_default_and_reads_the_real_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADMIN_API_KEY must come from the actual process env, not a settings.yaml default.

    Regression test: a profile default here would always win over the real env var,
    silently defeating the shared admin credential.
    """
    monkeypatch.setenv("ADMIN_API_KEY", "a-different-key")

    config = get_env_config()

    assert config.ADMIN_API_KEY.get_secret_value() == "a-different-key"


def test_auth_disabled_has_no_yaml_default_and_reads_the_real_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTH_DISABLED must come from the actual process env, not a settings.yaml default."""
    monkeypatch.setenv("AUTH_DISABLED", "true")

    config = get_env_config()

    assert config.AUTH_DISABLED == "true"


def test_tts_provider_has_no_yaml_default_and_reads_the_real_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTS_PROVIDER must come from the actual process env, not a settings.yaml default.

    Regression test for a real incident: `environments.base` used to hardcode
    `TTS_PROVIDER: "piper"` (and STT_PROVIDER/WHISPER_MODEL/PIPER_VOICE_ID*), which —
    same as ADMIN_API_KEY above — always wins over a real env var in this loader.
    docker-compose.yml's `${VOICE_TTS_PROVIDER:-piper}` override silently had zero
    effect as a result: setting VOICE_TTS_PROVIDER=elevenlabs in `.env` still got
    Piper, with no error anywhere to reveal why.
    """
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")

    config = get_env_config()

    assert config.TTS_PROVIDER == "elevenlabs"


def test_stt_provider_has_no_yaml_default_and_reads_the_real_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """STT_PROVIDER must come from the actual process env, not a settings.yaml default."""
    monkeypatch.setenv("STT_PROVIDER", "deepgram")

    config = get_env_config()

    assert config.STT_PROVIDER == "deepgram"
