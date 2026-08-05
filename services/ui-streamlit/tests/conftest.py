"""Shared test fixtures for the ui-streamlit test suite."""

from __future__ import annotations

import sys
from collections.abc import Generator

import pytest

import ui_streamlit.core.logger as _logger_module


class FakeSecret:
    """Minimal stand-in for pydantic.SecretStr, used wherever tests need a fake secret value."""

    def __init__(self, value: str = "example-secret-key-0123456789") -> None:
        """Store the plaintext value this fake secret should reveal."""
        self._value = value

    def get_secret_value(self) -> str:
        """Return the fake secret's plaintext value."""
        return self._value


@pytest.fixture(autouse=True)
def reset_singletons() -> Generator[None]:
    """Clear lru_cache singletons and module-level state between tests."""
    # Clear cached module to force re-import
    sys.modules.pop("ui_streamlit.data_model", None)
    # Reset loguru configuration flag so configure_logger() works fresh each test
    _logger_module._configured = False

    yield

    # Post-test cleanup: reset any cached settings
    try:
        from ui_streamlit.data_model import get_env_config

        get_env_config.cache_clear()
    except ImportError:
        pass
