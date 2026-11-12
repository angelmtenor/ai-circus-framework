"""
test_env_profiles.py
--------------------

Tests for the environment-aware configuration loading.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import pytest

from crm_mcp.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache before each test; URLs and the client secret have no profile default."""
    get_env_config.cache_clear()
    monkeypatch.setenv("KEYCLOAK_ISSUER", "http://idp.test/realms/test")
    monkeypatch.setenv("KEYCLOAK_JWKS_URL", "http://idp.test/realms/test/certs")
    monkeypatch.setenv("MCP_CLIENT_SECRET", "test-secret")


def test_get_env_config_default_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no APP_ENVIRONMENT set, the 'local' profile (base defaults) is used."""
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"
    assert config.API_AUDIENCE == "crm-api"


@pytest.mark.parametrize("profile", ["local", "staging", "production"])
def test_get_env_config_reads_app_environment(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """APP_ENVIRONMENT selects the active profile (all profiles currently fall back to base defaults)."""
    monkeypatch.setenv("APP_ENVIRONMENT", profile)

    config = get_env_config()

    assert config.LOG_LEVEL == "INFO"


def test_get_env_config_explicit_env_overrides_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit `env=` argument takes priority over the APP_ENVIRONMENT variable."""
    monkeypatch.setenv("APP_ENVIRONMENT", "production")

    config = get_env_config(env="staging")

    assert config.LOG_LEVEL == "INFO"
