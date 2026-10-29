"""
data_model.py
-----------
Generated Pydantic Settings model from settings.yaml.
DO NOT EDIT DIRECTLY. Run 'make generate-data-model' to update.

Author: Angel Martinez-Tenor
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
    SCENARIOS: str | None = Field(
        description="Comma-separated data_product slugs to transform (empty = every data_product)", default=None
    )
    SCENARIOS_DIR: str = Field(description="Path to the scenarios/ directory (one subdirectory per scenario.yaml)")
    POSTGRES_HOST: str = Field(
        description="Postgres hostname (docker service name in-container, localhost for local dev)"
    )
    POSTGRES_PORT: str = Field(description="Postgres port")
    POSTGRES_DB: str = Field(description="Warehouse database (bronze in, silver/gold out)")
    POSTGRES_USER: str = Field(description="Postgres user")
    POSTGRES_PASSWORD: SecretStr = Field(description="Postgres password")
    DATA_PSEUDONYM_KEY: SecretStr = Field(
        description="HMAC key pseudonymising personal identifiers in silver (kept in a private table, never in SQL)"
    )
    KAFKA_BOOTSTRAP_SERVERS: str = Field(
        description="Kafka bootstrap server(s), host:port (pipeline events for the live view)"
    )
    TRANSFORM_TARGETS: str | None = Field(
        description="dbt targets in order: postgres (core, default when unset), optionally clickhouse (analytics)",
        default=None,
    )
    CLICKHOUSE_HOST: str | None = Field(description="ClickHouse host (analytics tier only)", default=None)
    CLICKHOUSE_USER: str | None = Field(
        description="ClickHouse user owning the data products' databases (analytics tier only)", default=None
    )
    CLICKHOUSE_PASSWORD: SecretStr | None = Field(description="ClickHouse password (analytics tier only)", default=None)
    OBJECT_STORE_ENDPOINT: str = Field(
        description="SeaweedFS/S3 endpoint URL (dbt manifest/catalog/run results for the catalogue)"
    )
    OBJECT_STORE_ACCESS_KEY: str = Field(
        description="SeaweedFS access key (must match OBJECT_STORE_ACCESS_KEY in the repo root .env)"
    )
    OBJECT_STORE_SECRET_KEY: SecretStr = Field(
        description="SeaweedFS secret key (must match OBJECT_STORE_SECRET_KEY in the repo root .env)"
    )
    SUPERSET_URL: str | None = Field(description="Superset base URL (bi tier: the bi-sync Job)", default=None)
    SUPERSET_ADMIN_PASSWORD: SecretStr | None = Field(
        description="Superset admin password (bi tier: the bi-sync Job)", default=None
    )
    DATA_FALLBACK_ORG_ID: str = Field(
        description="The shared demo tenant BI dashboards show (Superset row-level security base filter)"
    )
    DBT_THREADS: str = Field(description="dbt worker threads")
    ARTIFACTS_DIR: str = Field(
        description="Where dbt writes target/ and logs/ per data product (scenarios/ is mounted read-only)"
    )

    @field_validator("TRANSFORM_TARGETS", mode="after")
    @classmethod
    def validate_transform_targets(cls, v: Any) -> Any:
        """Validate field format via regex."""
        if v is None:
            return v
        val = v.get_secret_value() if hasattr(v, "get_secret_value") else str(v)
        if not val:
            return None
        if not re.match(r"^postgres(,clickhouse)?$", val):
            raise ValueError("TRANSFORM_TARGETS must be postgres or postgres,clickhouse.")
        return v


_SOURCE_YAML_HASH = "0880d5e5a5654a42827645331c2fe29ffe9c871919d333eaea8ff957a2289943"


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
