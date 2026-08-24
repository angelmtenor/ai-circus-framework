"""
data_model.py
-----------
Generated Pydantic Settings model from settings.yaml.
DO NOT EDIT DIRECTLY. Run 'make generate-data-model' to update.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvConfig(BaseSettings):
    """Environment configuration model."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )
    LOG_LEVEL: str = Field(description="Application log level (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    HTTP_PORT: str = Field(description="Port the FastAPI app listens on")
    ASSISTANT_SERVICE_URL: str = Field(
        description="Base URL of the assistant service (backs tabular_ml voice scenarios)"
    )
    RAG_AGENT_SERVICE_URL: str = Field(
        description="Base URL of the rag-agent service (backs conversational_rag voice scenarios)"
    )
    FORM_AGENT_SERVICE_URL: str = Field(
        description="Base URL of the form-agent service (backs assisted_form voice scenarios)"
    )
    PLATFORM_REGISTRY_URL: str = Field(description="Base URL of the platform-registry service's entitlement-check API")
    STT_PROVIDER: str = Field(description="Speech-to-text backend: 'whisper' (local, default) or 'deepgram' (cloud)")
    TTS_PROVIDER: str = Field(
        description="Text-to-speech backend: 'piper' (local, default), 'elevenlabs', or 'cartesia'"
    )
    WHISPER_MODEL: str = Field(
        description="faster-whisper model size/name used when STT_PROVIDER=whisper (e.g. base, small, distil-large-v3)"
    )
    PIPER_VOICE_ID: str = Field(
        description="Piper voice id for English, used when TTS_PROVIDER=piper (e.g. en_US-lessac-medium)"
    )
    PIPER_VOICE_ID_ES: str = Field(
        description="Piper voice id for Spanish, used when TTS_PROVIDER=piper (e.g. es_ES-davefx-medium)"
    )
    DEEPGRAM_API_KEY: SecretStr | None = Field(
        description="API key for Deepgram STT, required only when STT_PROVIDER=deepgram", default=None
    )
    ELEVENLABS_API_KEY: SecretStr | None = Field(
        description="API key for ElevenLabs TTS, required only when TTS_PROVIDER=elevenlabs", default=None
    )
    ELEVENLABS_VOICE_ID: str | None = Field(
        description="ElevenLabs voice id, required only when TTS_PROVIDER=elevenlabs", default=None
    )
    CARTESIA_API_KEY: SecretStr | None = Field(
        description="API key for Cartesia TTS, required only when TTS_PROVIDER=cartesia", default=None
    )
    CARTESIA_VOICE_ID: str | None = Field(
        description="Cartesia voice id, required only when TTS_PROVIDER=cartesia", default=None
    )
    AUTH_DISABLED: str = Field(
        description="DEV ONLY: skip token/entitlement checks. Must be false beyond local iteration."
    )
    CORS_ALLOWED_ORIGINS: str = Field(
        description="Comma-separated origins ui-react is allowed to call this API from (never '*' beyond local dev)"
    )
    ADMIN_API_KEY: SecretStr = Field(
        description="Shared admin bearer token — resolves to the 'admin' org, entitled to every scenario"
    )
    ENGINEERING_DEMO_API_KEY: SecretStr | None = Field(
        description="Optional demo bearer token for 'engineering-demo' org (mpm/electric_motor/energy_building only)",
        default=None,
    )
    DEV_ORG_ID: str = Field(description="Org id used for every request when AUTH_DISABLED=true")
    LOGTO_ISSUER: str | None = Field(
        description="Logto OIDC issuer, e.g. http://logto.localhost/oidc (required unless AUTH_DISABLED=true)",
        default=None,
    )
    LOGTO_JWKS_URL: str | None = Field(
        description="Logto JWKS endpoint, e.g. http://logto.localhost/oidc/jwks (required unless AUTH_DISABLED=true)",
        default=None,
    )
    LOGTO_API_RESOURCE_INDICATOR: str | None = Field(
        description="Expected token audience — the API resource registered in Logto for this platform's backend",
        default=None,
    )


_SOURCE_YAML_HASH = "d90bcf80bdedb80390668ff438bf4bcd6a25768f16ea98236c9b222a46cfe6b6"


EnvConfig.model_rebuild()


def _load_env_overrides(env: str) -> dict[str, Any]:
    """Load per-environment non-secret defaults from settings.yaml.

    Merges the base non-secret defaults with the profile-specific
    overrides defined under ``environments.<env>`` in settings.yaml.
    """
    config_path = Path(__file__).parent.parent.parent / "settings.yaml"
    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    base: dict[str, Any] = data.get("environments", {}).get("base", {}).copy()
    base.update(data.get("environments", {}).get(env, {}))
    return base


@lru_cache(maxsize=4)
def get_env_config(env: str | None = None) -> EnvConfig:
    """Return the validated environment configuration for the given profile.

    The active profile is resolved from the *env* argument, then the
    ``APP_ENVIRONMENT`` environment variable, defaulting to ``"local"``.
    Valid profiles: local, docker, staging, production.
    """
    active_env = env or os.getenv("APP_ENVIRONMENT", "local")
    overrides = _load_env_overrides(active_env)
    return EnvConfig(**overrides)


def main() -> None:
    """Display the loaded configuration (redacted)."""
    env_config = get_env_config()
    print("--- Loaded Configuration ---")  # ruff: ignore[print]
    for field in EnvConfig.model_fields:
        val = getattr(env_config, field)
        if hasattr(val, "get_secret_value"):
            val = "****" + val.get_secret_value()[-4:] if val and val.get_secret_value() else "None"
        print(f"{field}: {val}")  # ruff: ignore[print]


if __name__ == "__main__":
    main()
