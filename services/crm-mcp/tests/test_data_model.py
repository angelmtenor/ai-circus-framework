"""Tests for lazy environment configuration loading."""

from __future__ import annotations

import importlib
import sys

import pytest


def test_data_model_import_does_not_validate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing the module should not instantiate EnvConfig eagerly."""
    monkeypatch.delenv("MCP_CLIENT_SECRET", raising=False)
    sys.modules.pop("crm_mcp.data_model", None)

    module = importlib.import_module("crm_mcp.data_model")

    assert hasattr(module, "get_env_config")
    assert not hasattr(module, "env_config")
