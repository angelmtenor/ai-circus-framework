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
    SCENARIOS: str = Field(
        description="Comma-separated tabular_ml scenario slugs this instance serves; empty/unset = every scenario"
    )
    SCENARIOS_DIR: str = Field(description="Path to the scenarios/ directory (one subdirectory per scenario.yaml)")
    MINIO_ENDPOINT: str = Field(
        description="MinIO/S3 endpoint URL (docker service name in-container, *.localhost via Traefik for local dev)"
    )
    MINIO_ACCESS_KEY: str = Field(description="MinIO access key (must match MINIO_ROOT_USER in the repo root .env)")
    MINIO_SECRET_KEY: SecretStr = Field(
        description="MinIO secret key (must match MINIO_ROOT_PASSWORD in the repo root .env)"
    )
    LLM_GATEWAY_URL: str = Field(description="Base URL of the llm-gateway service's OpenAI-compatible API")
    LLM_GATEWAY_API_KEY: SecretStr = Field(description="API key presented to llm-gateway (its LITELLM_MASTER_KEY)")
    LLM_MODEL: str = Field(
        description="Model name to request from llm-gateway (must be in its litellm_config.yaml model_list)"
    )
    PLATFORM_REGISTRY_URL: str = Field(description="Base URL of the platform-registry service's entitlement-check API")
    AUTH_DISABLED: str = Field(
        description="DEV ONLY: skip token/entitlement checks. Must be false beyond local iteration."
    )
    CORS_ALLOWED_ORIGINS: str = Field(
        description="Comma-separated origins ui-react is allowed to call this API from (never '*' beyond local dev)"
    )
    ADMIN_API_KEY: SecretStr = Field(
        description="Shared admin bearer token — resolves to the 'admin' org, entitled to every scenario"
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


_SOURCE_YAML_HASH = "413f402d5cf697a3425b021a7311d4c4378dba6223980738b73ed93372fb1a46"


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
