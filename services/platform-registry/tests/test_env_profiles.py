"""
test_env_profiles.py
--------------------

Tests for the environment-aware configuration loading.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import pytest

from platform_registry.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache and set the one mandatory secret every profile needs."""
    get_env_config.cache_clear()
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-password")
    # CORS_ALLOWED_ORIGINS intentionally has no settings.yaml default (see
    # settings.yaml) — it must come from a real env var.
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://react.localhost")


def test_get_env_config_default_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no APP_ENVIRONMENT set, the 'local' profile is used."""
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.POSTGRES_HOST == "localhost"
    assert config.SCENARIOS_DIR == "../../scenarios"


def test_get_env_config_docker_profile() -> None:
    """The 'docker' profile points at the in-network Postgres hostname and mounted scenarios dir."""
    config = get_env_config(env="docker")

    assert config.POSTGRES_HOST == "postgres"
    assert config.SCENARIOS_DIR == "/app/scenarios"


@pytest.mark.parametrize("profile", ["local", "docker"])
def test_get_env_config_reads_app_environment(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """APP_ENVIRONMENT selects the active profile; base defaults always apply.

    `staging`/`production` are intentionally left as empty placeholder profiles in
    settings.yaml (nothing is deployed there yet) — POSTGRES_HOST/SCENARIOS_DIR would
    need real values filled in before either becomes usable, so they're not covered here.
    """
    monkeypatch.setenv("APP_ENVIRONMENT", profile)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.POSTGRES_DB == "platform"


def test_get_env_config_explicit_env_overrides_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit `env=` argument takes priority over the APP_ENVIRONMENT variable."""
    monkeypatch.setenv("APP_ENVIRONMENT", "docker")

    config = get_env_config(env="local")

    assert config.POSTGRES_HOST == "localhost"
