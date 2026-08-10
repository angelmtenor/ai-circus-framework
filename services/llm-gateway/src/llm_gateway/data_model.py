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
    HTTP_PORT: str = Field(description="Port the LiteLLM proxy listens on")
    LITELLM_CONFIG_PATH: str = Field(description="Path to litellm_config.yaml (model routing / general settings)")
    LITELLM_MASTER_KEY: SecretStr = Field(
        description="Master API key callers must present to the gateway (Bearer token)"
    )
    OPENAI_API_KEY: SecretStr | None = Field(
        description="OpenAI API key (only needed if litellm_config.yaml routes to an openai/* model)", default=None
    )
    GOOGLE_API_KEY: SecretStr | None = Field(
        description="Google API key (only needed if litellm_config.yaml routes to a gemini/* model)", default=None
    )
    AZURE_OPENAI_API_KEY: SecretStr | None = Field(
        description="Azure OpenAI API key (only needed if litellm_config.yaml routes to an azure/* model)", default=None
    )
    AZURE_OPENAI_API_BASE: str | None = Field(
        description="Azure OpenAI resource endpoint, e.g. https://<resource>.openai.azure.com (azure-gpt4o model only)",
        default=None,
    )
    DEEPSEEK_API_KEY: SecretStr | None = Field(
        description="DeepSeek API key (only needed if litellm_config.yaml routes to the deepseek-chat model)",
        default=None,
    )
    GROQ_API_KEY: SecretStr | None = Field(
        description="GroqCloud API key (only needed if litellm_config.yaml routes to the groq-llama model)",
        default=None,
    )
    OPENROUTER_API_KEY: SecretStr | None = Field(
        description="OpenRouter API key (only needed if litellm_config.yaml routes to the openrouter model)",
        default=None,
    )
    ANTHROPIC_API_KEY: SecretStr | None = Field(
        description="Anthropic API key (only needed if litellm_config.yaml routes to the claude-haiku model)",
        default=None,
    )
    OLLAMA_API_BASE: str | None = Field(
        description="Base URL of the optional Ollama instance (llama3 model only); unset/not started = unreachable",
        default=None,
    )


_SOURCE_YAML_HASH = "1402ec30cea72d5e111391cb78c58d0993fd9494c343810483e77fe2c29016fc"


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
