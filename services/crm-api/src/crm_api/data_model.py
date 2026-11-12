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
    HTTP_PORT: str = Field(description="Port the API listens on")
    KEYCLOAK_ISSUER: str = Field(description="Keycloak realm issuer every accepted token must carry")
    KEYCLOAK_JWKS_URL: str = Field(description="Keycloak realm JWKS endpoint (in-cluster URL)")
    API_AUDIENCE: str = Field(
        description="The audience a token must name to be accepted here: this API's Keycloak client id"
    )
    SECURITY_LEVEL: str | None = Field(
        description="2: bearer token for this API; 3: DPoP-bound token, checked live, over mutual TLS (ADR 0025)",
        default="2",
    )
    KEYCLOAK_INTROSPECTION_URL: str | None = Field(
        description="Keycloak introspection endpoint (in-cluster URL), asked about every token at level 3", default=None
    )
    API_CLIENT_SECRET: SecretStr | None = Field(
        description="This API's Keycloak client secret (CRM_API_CLIENT_SECRET), to introspect its tokens at level 3",
        default=None,
    )
    TLS_PORT: str | None = Field(
        description="Mutual-TLS port of the API at level 3 (HTTP_PORT then serves only /healthz and /metrics)",
        default="8443",
    )
    TLS_CERT_FILE: str | None = Field(
        description="This API's server certificate (PEM) for the mutual-TLS listener", default=None
    )
    TLS_KEY_FILE: str | None = Field(description="Private key of the server certificate (PEM)", default=None)
    TLS_CLIENT_CA_FILE: str | None = Field(
        description="The only CA whose client certificates may connect: the one shared with its MCP server",
        default=None,
    )


_SOURCE_YAML_HASH = "de6cc53995c71a6c0f6973dfb9df99f237691ede11c02b810f992428ed8b343d"


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
