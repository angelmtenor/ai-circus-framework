"""
test_env_profiles.py
--------------------

Tests for the environment-aware configuration loading.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import pytest

from prediction.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache and set the mandatory fields with no profile default."""
    get_env_config.cache_clear()
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")
    # AUTH_DISABLED intentionally has no settings.yaml default (see settings.yaml) —
    # it must come from a real env var so docker-compose's passthrough actually works.
    monkeypatch.setenv("AUTH_DISABLED", "false")


def test_get_env_config_default_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no APP_ENVIRONMENT set, the 'local' profile is used."""
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.SCENARIOS_DIR == "../../scenarios"
    assert config.MINIO_ENDPOINT == "http://minio.localhost"
    assert config.PLATFORM_REGISTRY_URL == "http://localhost:8010"


def test_get_env_config_docker_profile() -> None:
    """The 'docker' profile points at in-network hostnames and the mounted scenarios dir."""
    config = get_env_config(env="docker")

    assert config.SCENARIOS_DIR == "/app/scenarios"
    assert config.MINIO_ENDPOINT == "http://minio:9000"
    assert config.PLATFORM_REGISTRY_URL == "http://platform-registry:8000"


@pytest.mark.parametrize("profile", ["local", "docker"])
def test_get_env_config_reads_app_environment(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """APP_ENVIRONMENT selects the active profile; base defaults always apply.

    `staging`/`production` are intentionally left as empty placeholder profiles in
    settings.yaml (nothing is deployed there yet), so they're not covered here.
    """
    monkeypatch.setenv("APP_ENVIRONMENT", profile)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.AUTH_DISABLED == "false"


def test_auth_disabled_has_no_yaml_default_and_reads_the_real_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTH_DISABLED must come from the actual process env, not a settings.yaml default.

    Regression test: a profile default here would always win over docker-compose's
    `${AUTH_DISABLED:-false}` passthrough, silently defeating the dev-only bypass toggle.
    """
    monkeypatch.setenv("AUTH_DISABLED", "true")

    config = get_env_config()

    assert config.AUTH_DISABLED == "true"


def test_get_env_config_explicit_env_overrides_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit `env=` argument takes priority over the APP_ENVIRONMENT variable."""
    monkeypatch.setenv("APP_ENVIRONMENT", "docker")

    config = get_env_config(env="local")

    assert config.MINIO_ENDPOINT == "http://minio.localhost"
