"""Tests for settings.yaml profile selection (APP_ENVIRONMENT)."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from data_transform.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Clear the lru_cache and set the secrets that have no profile default."""
    get_env_config.cache_clear()
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-password")
    monkeypatch.setenv("OBJECT_STORE_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("OBJECT_STORE_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("DATA_PSEUDONYM_KEY", "test-pseudonym-key")
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
    yield
    get_env_config.cache_clear()


def test_get_env_config_default_local() -> None:
    """With no APP_ENVIRONMENT set, the 'local' profile is used."""
    config = get_env_config()
    assert config.POSTGRES_HOST == "localhost"
    assert config.POSTGRES_DB == "data_platform"
    assert config.TRANSFORM_TARGETS is None  # set per pod; unset = postgres


def test_get_env_config_docker_profile() -> None:
    """The 'docker' profile points at the in-network hostnames."""
    config = get_env_config(env="docker")
    assert config.POSTGRES_HOST == "postgres"
    assert config.KAFKA_BOOTSTRAP_SERVERS == "kafka:9092"


def test_get_env_config_explicit_env_overrides_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit env argument beats APP_ENVIRONMENT."""
    monkeypatch.setenv("APP_ENVIRONMENT", "docker")
    assert get_env_config(env="local").POSTGRES_HOST == "localhost"
