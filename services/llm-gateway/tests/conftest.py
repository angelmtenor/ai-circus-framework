"""Shared test fixtures for the llm-gateway test suite."""

from __future__ import annotations

import os
import sys

# litellm calls load_dotenv() at import time unless LITELLM_MODE=PRODUCTION — run from
# this directory that walks up to the repo-root .env and loads the real provider and
# Langfuse keys into the test process, making tests environment-dependent (with
# Langfuse keys present, test_main_execs_litellm_with_resolved_config took the
# tracing branch and failed). Set before any test module imports litellm.
os.environ["LITELLM_MODE"] = "PRODUCTION"
# budget_hook.py builds its enforcer at import time from get_env_config(), whose one
# mandatory field this is — previously satisfied only by that leaked real .env.
os.environ.setdefault("LITELLM_MASTER_KEY", "test-only-master-key")
from collections.abc import Generator

import pytest

import llm_gateway.core.logger as _logger_module


class FakeSecret:
    """Minimal stand-in for pydantic.SecretStr, used wherever tests need a fake secret value."""

    def __init__(self, value: str = "example-secret-key-0123456789") -> None:
        """Store the plaintext value this fake secret should reveal."""
        self._value = value

    def get_secret_value(self) -> str:
        """Return the fake secret's plaintext value."""
        return self._value


@pytest.fixture(autouse=True)
def no_ambient_langfuse_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The root Makefile exports the repo's .env into every `make` it runs (so `make
    check-all` hands real Langfuse keys to this process), and app.main() switches on
    tracing whenever both are set — tests that want tracing set them explicitly.
    """
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def reset_singletons() -> Generator[None]:
    """Clear lru_cache singletons and module-level state between tests."""
    # Clear cached module to force re-import
    sys.modules.pop("llm_gateway.data_model", None)
    # Reset loguru configuration flag so configure_logger() works fresh each test
    _logger_module._configured = False

    yield

    # Post-test cleanup: reset any cached settings
    try:
        from llm_gateway.data_model import get_env_config

        get_env_config.cache_clear()
    except ImportError:
        pass
