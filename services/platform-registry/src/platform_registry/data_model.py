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
    POSTGRES_HOST: str = Field(
        description="Postgres hostname (docker service name in-container, localhost for local dev)"
    )
    POSTGRES_PORT: str = Field(description="Postgres port")
    POSTGRES_DB: str = Field(description="Postgres database name for the platform schema")
    POSTGRES_USER: str = Field(description="Postgres user")
    POSTGRES_PASSWORD: SecretStr = Field(description="Postgres password")
    SCENARIOS_DIR: str = Field(
        description="Path to the scenarios/ directory (one subdirectory per scenario.yaml) to seed from"
    )
    LOGTO_ENDPOINT: str | None = Field(
        description="Logto's public endpoint, e.g. http://logto.localhost (used only by the entitlement sync tool)",
        default=None,
    )
    LOGTO_M2M_APP_ID: str | None = Field(
        description="Machine-to-machine application ID for calling Logto's Management API (sync tool only)",
        default=None,
    )
    LOGTO_M2M_APP_SECRET: SecretStr | None = Field(
        description="Machine-to-machine application secret for calling Logto's Management API (sync tool only)",
        default=None,
    )
    CORS_ALLOWED_ORIGINS: str = Field(
        description="Comma-separated origins ui-react is allowed to call this API from (never '*' beyond local dev)"
    )
    ADMIN_API_KEY: SecretStr = Field(
        description="Bearer token required on /llm-settings/* (shared with other services' admin-key bypass)"
    )
    LLM_GATEWAY_URL: str = Field(description="Base URL of the llm-gateway service's OpenAI-compatible + admin API")
    LLM_GATEWAY_API_KEY: SecretStr = Field(
        description="API key presented to llm-gateway (its LITELLM_MASTER_KEY) for admin calls"
    )


_SOURCE_YAML_HASH = "a6176a3626a69b7368dd07847bd89875e1f4372f8a0527c7502aa0415360fd2e"


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
