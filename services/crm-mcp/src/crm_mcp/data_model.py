"""
data_model.py
-----------
Generated Pydantic Settings model from settings.yaml.
DO NOT EDIT DIRECTLY. Run 'make generate-data-model' to update.

Author: Angel Martinez-Tenor
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
    HTTP_PORT: str = Field(description="Port the MCP server listens on")
    KEYCLOAK_ISSUER: str = Field(description="Keycloak realm issuer every accepted token must carry")
    KEYCLOAK_JWKS_URL: str = Field(description="Keycloak realm JWKS endpoint (in-cluster URL)")
    KEYCLOAK_TOKEN_URL: str = Field(description="Keycloak realm token endpoint for the token exchange (in-cluster URL)")
    MCP_CLIENT_ID: str = Field(
        description="This server's Keycloak client: the audience a user token (T1) must name, and who exchanges it"
    )
    MCP_CLIENT_SECRET: SecretStr = Field(
        description="This server's Keycloak client secret (CRM_MCP_CLIENT_SECRET in .env)"
    )
    API_AUDIENCE: str = Field(description="The API's Keycloak client: the only audience a per-API token (T2) may have")
    API_BASE_URL: str = Field(description="Base URL of the API this server calls (in-cluster)")
    POLICY_FILE: str = Field(description="Access policy: which tools each tenant and role may use (docs/adr/0022)")
    KEYCLOAK_INTROSPECTION_URL: str | None = Field(
        description="Keycloak introspection endpoint; default: KEYCLOAK_TOKEN_URL + /introspect", default=None
    )
    API_TLS_CA_FILE: str | None = Field(
        description="The CA shared with the API: the only one its server certificate may come from", default=None
    )
    API_TLS_CERT_FILE: str | None = Field(
        description="This server's client certificate for mutual TLS to the API (PEM)", default=None
    )
    API_TLS_KEY_FILE: str | None = Field(description="Private key of the client certificate (PEM)", default=None)


_SOURCE_YAML_HASH = "068dc8eabd11be05bc0c2daea971c65f138cdbd213dd189a902192efe88046ea"


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
    Valid profiles: local, staging, production.
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
