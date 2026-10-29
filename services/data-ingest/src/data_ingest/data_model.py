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
    POSTGRES_DB: str = Field(
        description="Warehouse database: bronze, hot, ops (and the dbt-built silver/gold) — one schema per data product"
    )
    SOURCES_DB: str = Field(
        description="Operational source database the data products' simulators play (ERP…), captured by CDC"
    )
    POSTGRES_USER: str = Field(
        description="Postgres user (needs CREATEDB and REPLICATION — the cluster's own user has both)"
    )
    POSTGRES_PASSWORD: SecretStr = Field(description="Postgres password")
    KAFKA_BOOTSTRAP_SERVERS: str = Field(description="Kafka bootstrap server(s), host:port")
    OBJECT_STORE_ENDPOINT: str = Field(description="SeaweedFS/S3 endpoint URL (landing bucket + Iceberg lake)")
    OBJECT_STORE_ACCESS_KEY: str = Field(
        description="SeaweedFS access key (must match OBJECT_STORE_ACCESS_KEY in the repo root .env)"
    )
    OBJECT_STORE_SECRET_KEY: SecretStr = Field(
        description="SeaweedFS secret key (must match OBJECT_STORE_SECRET_KEY in the repo root .env)"
    )
    ADMIN_API_KEY: SecretStr = Field(
        description="Bearer token required on the status/trigger routes (service-to-service and admin)"
    )
    SCENARIOS_DIR: str = Field(description="Path to the scenarios/ directory (one subdirectory per scenario.yaml)")
    SCENARIOS: str | None = Field(
        description="Comma-separated data_product slugs this instance serves (empty = every data_product)", default=None
    )
    LANDING_SCAN_SECONDS: str = Field(description="How often the landing bucket is scanned for new file deliveries")
    CDC_POLL_SECONDS: str = Field(description="How often the CDC replication slot is drained")
    LAKE_ARCHIVE_SECONDS: str = Field(description="How often new bronze rows are appended to the Iceberg lake")


_SOURCE_YAML_HASH = "991cb51ee51a99571549d9385e83d150f1cb40b672680e4183c6c75c4a0a3bd1"


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
