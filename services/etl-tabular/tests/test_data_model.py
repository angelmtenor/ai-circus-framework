"""Tests for lazy environment configuration loading."""

from __future__ import annotations

import importlib
import sys

import pytest


def test_data_model_import_does_not_validate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing the module should not instantiate EnvConfig eagerly."""
    monkeypatch.delenv("EXAMPLE_SERVICE_API_KEY", raising=False)
    sys.modules.pop("etl_tabular.data_model", None)

    module = importlib.import_module("etl_tabular.data_model")

    assert hasattr(module, "get_env_config")
    assert not hasattr(module, "env_config")
