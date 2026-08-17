"""
test_env_profiles.py
--------------------

Tests for the environment-aware configuration loading.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import pytest

from etl_vectorize.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache and set the mandatory fields with no profile default."""
    get_env_config.cache_clear()
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")


def test_get_env_config_default_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no APP_ENVIRONMENT set, the 'local' profile is used."""
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.SCENARIOS == ""
    assert config.SCENARIOS_DIR == "../../scenarios"
    assert config.MINIO_ENDPOINT == "http://minio.localhost"
    assert config.QDRANT_URL == "http://localhost:6333"


def test_get_env_config_docker_profile() -> None:
    """The 'docker' profile points at the in-network hostnames and the mounted scenarios dir."""
    config = get_env_config(env="docker")

    assert config.SCENARIOS_DIR == "/app/scenarios"
    assert config.MINIO_ENDPOINT == "http://minio:9000"
    assert config.QDRANT_URL == "http://qdrant:6333"


@pytest.mark.parametrize("profile", ["local", "docker"])
def test_get_env_config_reads_app_environment(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """APP_ENVIRONMENT selects the active profile; base defaults always apply.

    `staging`/`production` are intentionally left as empty placeholder profiles in
    settings.yaml (nothing is deployed there yet), so they're not covered here.
    """
    monkeypatch.setenv("APP_ENVIRONMENT", profile)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.ORG_ID == "admin"


def test_get_env_config_explicit_env_overrides_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit `env=` argument takes priority over the APP_ENVIRONMENT variable."""
    monkeypatch.setenv("APP_ENVIRONMENT", "docker")

    config = get_env_config(env="local")

    assert config.MINIO_ENDPOINT == "http://minio.localhost"


def test_scenarios_yaml_default_beats_a_real_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documents a known, accepted residual limitation — see etl-tabular/settings.yaml's comment."""
    monkeypatch.setenv("SCENARIOS", "docs_rag,other_docs")

    config = get_env_config()

    assert config.SCENARIOS == ""
