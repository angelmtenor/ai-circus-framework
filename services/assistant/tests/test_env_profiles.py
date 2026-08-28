"""
test_env_profiles.py
--------------------

Tests for the environment-aware configuration loading.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import pytest

from assistant.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache and set the mandatory fields with no profile default."""
    get_env_config.cache_clear()
    monkeypatch.setenv("OBJECT_STORE_SECRET_KEY", "test-secret")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "test-master-key")
    # AUTH_DISABLED, ADMIN_API_KEY, and LLM_MODEL intentionally have no settings.yaml
    # default (see settings.yaml) — they must come from real env vars.
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://react.localhost")
    # POSTGRES_PASSWORD has no settings.yaml default (a secret) — see POSTGRES_USER's
    # own base-profile comment on why the rest of the POSTGRES_* fields do have one.
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-postgres-password")


def test_get_env_config_default_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no APP_ENVIRONMENT set, the 'local' profile is used."""
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.SCENARIOS == ""
    assert config.SCENARIOS_DIR == "../../scenarios"
    assert config.LLM_GATEWAY_URL == "http://localhost:4000"
    assert config.PLATFORM_REGISTRY_URL == "http://localhost:8010"


def test_get_env_config_docker_profile() -> None:
    """The 'docker' profile points at in-network hostnames and the mounted scenarios dir."""
    config = get_env_config(env="docker")

    assert config.SCENARIOS_DIR == "/app/scenarios"
    assert config.OBJECT_STORE_ENDPOINT == "http://seaweedfs:8333"
    assert config.LLM_GATEWAY_URL == "http://llm-gateway:4000"
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


def test_llm_model_has_no_yaml_default_and_reads_the_real_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_MODEL must come from the actual process env, not a settings.yaml default.

    Regression test, same class as AUTH_DISABLED's/ADMIN_API_KEY's: a profile default
    here would always win over docker-compose's `LLM_MODEL: ${LLM_MODEL:-llama3}`
    passthrough, silently defeating per-deployment model swaps.
    """
    monkeypatch.setenv("LLM_MODEL", "a-different-model")

    config = get_env_config()

    assert config.LLM_MODEL == "a-different-model"


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

    assert config.OBJECT_STORE_ENDPOINT == "http://objectstore.localhost"


def test_admin_api_key_has_no_yaml_default_and_reads_the_real_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADMIN_API_KEY must come from the actual process env, not a settings.yaml default.

    Regression test, same class as AUTH_DISABLED's: a profile default here would
    always win over the real env var, silently defeating the shared admin credential.
    """
    monkeypatch.setenv("ADMIN_API_KEY", "a-different-key")

    config = get_env_config()

    assert config.ADMIN_API_KEY.get_secret_value() == "a-different-key"


def test_scenarios_yaml_default_beats_a_real_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documents a known, accepted residual limitation — see prediction/settings.yaml's
    comment (SCENARIOS has a yaml default, safe now that there's only one instance of
    this service, but that means a real env var override of it is silently ignored).
    """
    monkeypatch.setenv("SCENARIOS", "churn,mpm")

    config = get_env_config()

    assert config.SCENARIOS == ""
