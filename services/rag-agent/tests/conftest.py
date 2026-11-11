"""Shared test fixtures for the rag-agent test suite."""

from __future__ import annotations

import sys
from collections.abc import Generator
from typing import Any

import pytest
from ai_circus_shared.auth import Identity, TokenValidationError
from ai_circus_shared.entitlements import EntitlementDeniedError
from ai_circus_shared.retrieval import RetrievedChunk
from ai_circus_shared.scenario_schema import VectorStoreConfig

import rag_agent.core.logger as _logger_module
from rag_agent.core import mcp_door


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
    sys.modules.pop("rag_agent.data_model", None)
    # Reset loguru configuration flag so configure_logger() works fresh each test
    _logger_module._configured = False

    yield

    # Post-test cleanup: reset any cached settings
    try:
        from rag_agent.data_model import get_env_config

        get_env_config.cache_clear()
    except ImportError:
        pass


# The MCP door's callers (tests/test_mcp_door.py, tests/test_mcp_consumer.py): bearer → tenant.
TOKENS = {"Bearer t1": "org-1", "Bearer t2": "org-2", "Bearer t3": "org-3"}


def _fake_identity(authorization: str | None, scenario_slug: str) -> Identity:
    if authorization == "Bearer denied":
        raise EntitlementDeniedError(f"Not entitled to {scenario_slug}.")
    if authorization not in TOKENS:
        raise TokenValidationError("Missing or invalid bearer token.")
    return Identity(subject="u", org_id=TOKENS[authorization], roles=frozenset())


@pytest.fixture
def retrieved(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace vector search; record which tenant and how many passages each call asked for."""
    calls: list[dict[str, Any]] = []

    def _retrieve(_qdrant: Any, _embedder: Any, vector_store: VectorStoreConfig, org_id: str, query: str) -> list:
        calls.append({"org_id": org_id, "top_k": vector_store.top_k, "query": query})
        return [RetrievedChunk(text=f"passage for {org_id}", source="a/one.md", score=0.91234)]

    monkeypatch.setattr(mcp_door, "retrieve", _retrieve)
    monkeypatch.setattr(mcp_door, "_identity", _fake_identity)
    return calls
