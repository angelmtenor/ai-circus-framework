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
    KEYCLOAK_SERVER_URL: str | None = Field(
        description="Keycloak server root (distinct from the realm issuer; sync tool only)", default=None
    )
    KEYCLOAK_REALM: str = Field(description="Keycloak realm name, e.g. ai-circus")
    KEYCLOAK_M2M_CLIENT_ID: str | None = Field(
        description="Service-account client ID for calling Keycloak's Admin REST API (sync/provision tooling only)",
        default=None,
    )
    KEYCLOAK_M2M_CLIENT_SECRET: SecretStr | None = Field(
        description="Service-account client secret for calling Keycloak's Admin REST API (sync/provision tooling only)",
        default=None,
    )
    KEYCLOAK_OWNER_EMAIL: str | None = Field(
        description="Email for the real Keycloak user the provision-owner tool creates/finds (provision tool only)",
        default=None,
    )
    KEYCLOAK_OWNER_PASSWORD: SecretStr | None = Field(
        description="Password for the real Keycloak user the provision-owner tool creates (provision tool only)",
        default=None,
    )
    CORS_ALLOWED_ORIGINS: str = Field(
        description="Comma-separated origins ui-react is allowed to call this API from (never '*' beyond local dev)"
    )
    ADMIN_API_KEY: SecretStr = Field(
        description="Bearer token required on /llm-settings/* (shared with other services' admin-key bypass)"
    )
    ENGINEERING_DEMO_API_KEY: SecretStr | None = Field(
        description="Optional shared demo bearer token, checked by GET /auth/verify-engineering-demo-key", default=None
    )
    LLM_GATEWAY_URL: str = Field(description="Base URL of the llm-gateway service's OpenAI-compatible + admin API")
    LLM_GATEWAY_API_KEY: SecretStr = Field(
        description="API key presented to llm-gateway (its LITELLM_MASTER_KEY) for admin calls"
    )
    AUTH_DISABLED: str = Field(
        description="DEV ONLY: skip token checks on entitlement reads. Must be false beyond local iteration."
    )
    DEV_ORG_ID: str = Field(description="Org id used for every request when AUTH_DISABLED=true")
    KEYCLOAK_ISSUER: str | None = Field(
        description="Keycloak realm issuer (required unless AUTH_DISABLED=true)", default=None
    )
    KEYCLOAK_JWKS_URL: str | None = Field(
        description="Keycloak realm JWKS endpoint (required unless AUTH_DISABLED=true)", default=None
    )
    KEYCLOAK_AUDIENCE: str | None = Field(
        description="Expected token audience, registered via an Audience client-scope mapper in Keycloak", default=None
    )
    GOVERNANCE_EVENTS_ENABLED: str | None = Field(
        description="true = emit content-free CloudEvents governance events for admin changes", default=None
    )
    GOVERNANCE_EVENT_SINK: str | None = Field(
        description="Where governance events go: 'log' (default) or 'http' (HMAC-signed webhook via a Valkey outbox)",
        default=None,
    )
    GOVERNANCE_EVENT_NAMESPACE: str | None = Field(
        description="Reverse-DNS namespace of governance event types, '<namespace>.governance.<name>.v1'", default=None
    )
    GOVERNANCE_WEBHOOK_URL: str | None = Field(
        description="Webhook receiving governance events when GOVERNANCE_EVENT_SINK=http", default=None
    )
    GOVERNANCE_WEBHOOK_SECRET: SecretStr | None = Field(
        description="HMAC secret signing webhook deliveries (X-Governance-Signature); from the environment only",
        default=None,
    )
    GOVERNANCE_AUDIT_ENABLED: str | None = Field(
        description="true = append every admin mutation to the hash-chained audit_log and serve GET /governance/audit",
        default=None,
    )
    GOVERNANCE_CATALOG_ENABLED: str | None = Field(
        description="true = publish each AI system's declared risk to Valkey for the gateway's by_risk fail mode",
        default=None,
    )
    CACHE_URL: str | None = Field(
        description="Redis-protocol URL (Valkey) — governance event outbox and AI-system catalogue only", default=None
    )


_SOURCE_YAML_HASH = "ee4e268aaeb03bcef901515bd86d8864cab85e3d2d84a720299dc48de289249e"


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
