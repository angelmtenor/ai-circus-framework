"""
data_model.py
-----------
Generated Pydantic Settings model from settings.yaml.
DO NOT EDIT DIRECTLY. Run 'make generate-data-model' to update.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr, field_validator
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
    SCENARIOS: str = Field(
        description="Comma-separated conversational_rag scenario slugs this run processes; empty/unset = every scenario"
    )
    ORG_ID: str = Field(
        description="Tenant (Logto Organization id) whose documents this run processes — base default: ADMIN_ORG_ID"
    )
    SCENARIOS_DIR: str = Field(description="Path to the scenarios/ directory (one subdirectory per scenario.yaml)")
    OBJECT_STORE_ENDPOINT: str = Field(
        description="SeaweedFS/S3 endpoint URL (docker service name in-container, *.localhost via Traefik locally)"
    )
    OBJECT_STORE_ACCESS_KEY: str = Field(
        description="SeaweedFS access key (must match OBJECT_STORE_ACCESS_KEY in the repo root .env)"
    )
    OBJECT_STORE_SECRET_KEY: SecretStr = Field(
        description="SeaweedFS secret key (must match OBJECT_STORE_SECRET_KEY in the repo root .env)"
    )
    QDRANT_URL: str = Field(
        description="Qdrant endpoint URL (docker service name in-container, *.localhost via Traefik for local dev)"
    )
    EMBEDDING_PROVIDER: str | None = Field(
        description="Embedding backend: 'local' (sentence-transformers, no API key), 'gemini', or 'voyage'",
        default="local",
    )
    EMBEDDING_MODEL: str | None = Field(
        description="Model name override for the active EMBEDDING_PROVIDER; unset = that provider's own default",
        default=None,
    )
    LLM_GATEWAY_URL: str = Field(
        description="Base URL of llm-gateway's OpenAI-compatible API (only needed if EMBEDDING_PROVIDER=local)"
    )
    LLM_GATEWAY_API_KEY: SecretStr = Field(
        description="API key presented to llm-gateway (its LITELLM_MASTER_KEY); needed if EMBEDDING_PROVIDER=local"
    )
    GOOGLE_API_KEY: SecretStr | None = Field(
        description="Google API key (only needed if EMBEDDING_PROVIDER=gemini)", default=None
    )
    VOYAGE_API_KEY: SecretStr | None = Field(
        description="Voyage AI API key (only needed if EMBEDDING_PROVIDER=voyage)", default=None
    )

    @field_validator("EMBEDDING_PROVIDER", mode="after")
    @classmethod
    def validate_embedding_provider(cls, v: Any) -> Any:
        """Validate field format via regex."""
        if v is None:
            return v
        val = v.get_secret_value() if hasattr(v, "get_secret_value") else str(v)
        if not val:
            return None
        if not re.match(r"^(local|gemini|voyage)$", val):
            raise ValueError("EMBEDDING_PROVIDER must be one of: local, gemini, voyage")
        return v


_SOURCE_YAML_HASH = "f74a1ebb285fce37b4eec1e0a9270a5ca9effd70d85b1128f1cc123e2eeb231b"


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
