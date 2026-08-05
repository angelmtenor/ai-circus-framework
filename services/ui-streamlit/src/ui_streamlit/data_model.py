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
    HTTP_PORT: str = Field(description="Port the Streamlit server listens on")
    PLATFORM_REGISTRY_URL: str = Field(description="Base URL of the platform-registry service's entitlement-check API")
    PREDICTION_URL: str = Field(description="Base URL of the prediction service (churn scenario)")
    ASSISTANT_URL: str = Field(description="Base URL of the assistant service (churn scenario chat)")
    RAG_AGENT_URL: str = Field(description="Base URL of the rag-agent service (docs_rag scenario)")
    DEV_MODE: str = Field(
        description="DEV ONLY: skip Logto login, pick a fake org/roles. Must be false beyond local iteration."
    )
    DEV_ORG_ID: str = Field(description="Org id used when DEV_MODE=true")
    LOGTO_ISSUER: str | None = Field(
        description="Logto OIDC issuer, e.g. http://logto.localhost/oidc (required unless DEV_MODE=true)", default=None
    )
    LOGTO_JWKS_URL: str | None = Field(description="Logto JWKS endpoint (required unless DEV_MODE=true)", default=None)
    LOGTO_CLIENT_ID: str | None = Field(
        description="Logto application (client) id registered for this UI (required unless DEV_MODE=true)", default=None
    )
    LOGTO_CLIENT_SECRET: SecretStr | None = Field(
        description="Logto application client secret (required unless DEV_MODE=true)", default=None
    )
    LOGTO_REDIRECT_URI: str | None = Field(
        description="OIDC redirect URI registered in Logto for this UI, e.g. http://app.localhost/callback",
        default=None,
    )
    LOGTO_API_RESOURCE_INDICATOR: str | None = Field(
        description="API resource indicator this UI requests access tokens for (the backend services' audience)",
        default=None,
    )


_SOURCE_YAML_HASH = "79f952f1260d86af839ee09c547909b95c9735065c2fdf02c19d598d4c138bad"


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
