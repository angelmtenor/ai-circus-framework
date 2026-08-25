"""
test_env_profiles.py
--------------------

Tests for the environment-aware configuration loading.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import pytest

from etl_tabular.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache and set the mandatory fields with no profile default."""
    get_env_config.cache_clear()
    monkeypatch.setenv("OBJECT_STORE_SECRET_KEY", "test-secret")


def test_get_env_config_default_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no APP_ENVIRONMENT set, the 'local' profile is used."""
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.SCENARIOS == ""
    assert config.SCENARIOS_DIR == "../../scenarios"
    assert config.OBJECT_STORE_ENDPOINT == "http://objectstore.localhost"


def test_get_env_config_docker_profile() -> None:
    """The 'docker' profile points at the in-network SeaweedFS hostname and mounted scenarios dir."""
    config = get_env_config(env="docker")

    assert config.SCENARIOS_DIR == "/app/scenarios"
    assert config.OBJECT_STORE_ENDPOINT == "http://seaweedfs:8333"


@pytest.mark.parametrize("profile", ["local", "docker"])
def test_get_env_config_reads_app_environment(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """APP_ENVIRONMENT selects the active profile; base defaults always apply.

    `staging`/`production` are intentionally left as empty placeholder profiles in
    settings.yaml (nothing is deployed there yet), so they're not covered here.
    """
    monkeypatch.setenv("APP_ENVIRONMENT", profile)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.ORG_ID == "demo"


def test_get_env_config_explicit_env_overrides_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit `env=` argument takes priority over the APP_ENVIRONMENT variable."""
    monkeypatch.setenv("APP_ENVIRONMENT", "docker")

    config = get_env_config(env="local")

    assert config.OBJECT_STORE_ENDPOINT == "http://objectstore.localhost"


def test_scenarios_yaml_default_beats_a_real_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documents a known, accepted residual limitation — see prediction/settings.yaml's
    comment (SCENARIOS has a yaml default, safe now that there's only one instance of
    this service, but that means a real env var override of it is silently ignored).
    """
    monkeypatch.setenv("SCENARIOS", "churn,mpm")

    config = get_env_config()

    assert config.SCENARIOS == ""
