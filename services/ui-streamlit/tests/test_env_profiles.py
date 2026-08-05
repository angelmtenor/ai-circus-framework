"""
test_env_profiles.py
--------------------

Tests for the environment-aware configuration loading.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import pytest

from ui_streamlit.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache and set the mandatory field with no profile default."""
    get_env_config.cache_clear()
    # DEV_MODE intentionally has no settings.yaml default (see settings.yaml) — it
    # must come from a real env var so docker-compose's passthrough actually works.
    monkeypatch.setenv("DEV_MODE", "true")


def test_get_env_config_default_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no APP_ENVIRONMENT set, the 'local' profile is used."""
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.PLATFORM_REGISTRY_URL == "http://localhost:8010"
    assert config.PREDICTION_URL == "http://prediction.localhost"


def test_get_env_config_docker_profile() -> None:
    """The 'docker' profile points at the in-network hostnames."""
    config = get_env_config(env="docker")

    assert config.PLATFORM_REGISTRY_URL == "http://platform-registry:8000"
    assert config.PREDICTION_URL == "http://prediction:8000"
    assert config.ASSISTANT_URL == "http://assistant:8000"
    assert config.RAG_AGENT_URL == "http://rag-agent:8000"


@pytest.mark.parametrize("profile", ["local", "docker"])
def test_get_env_config_reads_app_environment(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """APP_ENVIRONMENT selects the active profile; base defaults always apply.

    `staging`/`production` are intentionally left as empty placeholder profiles in
    settings.yaml (nothing is deployed there yet), so they're not covered here.
    """
    monkeypatch.setenv("APP_ENVIRONMENT", profile)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.DEV_ORG_ID == "demo"


def test_dev_mode_has_no_yaml_default_and_reads_the_real_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEV_MODE must come from the actual process env, not a settings.yaml default.

    Regression test: a profile default here would always win over docker-compose's
    `${DEV_MODE:-false}` passthrough, silently defeating the dev-only bypass toggle.
    """
    monkeypatch.setenv("DEV_MODE", "false")

    config = get_env_config()

    assert config.DEV_MODE == "false"


def test_get_env_config_explicit_env_overrides_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit `env=` argument takes priority over the APP_ENVIRONMENT variable."""
    monkeypatch.setenv("APP_ENVIRONMENT", "docker")

    config = get_env_config(env="local")

    assert config.PREDICTION_URL == "http://prediction.localhost"
