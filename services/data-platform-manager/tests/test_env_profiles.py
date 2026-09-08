"""
test_env_profiles.py
--------------------

Tests for the environment-aware configuration loading.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import pytest

from data_platform_manager.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache and set the mandatory fields with no profile default."""
    get_env_config.cache_clear()
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-password")
    # CACHE_URL, CORS_ALLOWED_ORIGINS, ADMIN_API_KEY, LLM_GATEWAY_URL,
    # LLM_GATEWAY_API_KEY, OBJECT_STORE_ACCESS_KEY, and OBJECT_STORE_SECRET_KEY
    # intentionally have no settings.yaml default (see that file) — they must
    # come from real env vars, same reasoning as platform-registry's
    # ADMIN_API_KEY/CORS_ALLOWED_ORIGINS.
    monkeypatch.setenv("CACHE_URL", "redis://localhost:6379")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://react.localhost")
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://localhost:4000")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "test-master-key")
    monkeypatch.setenv("OBJECT_STORE_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("OBJECT_STORE_SECRET_KEY", "test-secret-key")


def test_get_env_config_default_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no APP_ENVIRONMENT set, the 'local' profile is used."""
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.POSTGRES_HOST == "localhost"
    assert config.CACHE_URL == "redis://localhost:6379"


def test_get_env_config_docker_profile() -> None:
    """The 'docker' profile points at the in-network Postgres/Valkey/llm-gateway hostnames."""
    config = get_env_config(env="docker")

    assert config.POSTGRES_HOST == "postgres"
    assert config.LLM_GATEWAY_URL == "http://llm-gateway:4000"


@pytest.mark.parametrize("profile", ["local", "docker"])
def test_get_env_config_reads_app_environment(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """APP_ENVIRONMENT selects the active profile; base defaults always apply.

    `staging`/`production` are intentionally left as empty placeholder profiles in
    settings.yaml (nothing is deployed there yet), same as every other service here.
    """
    monkeypatch.setenv("APP_ENVIRONMENT", profile)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.POSTGRES_DB == "data_platform_manager"


def test_get_env_config_explicit_env_overrides_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit `env=` argument takes priority over the APP_ENVIRONMENT variable."""
    monkeypatch.setenv("APP_ENVIRONMENT", "docker")

    config = get_env_config(env="local")

    assert config.POSTGRES_HOST == "localhost"
