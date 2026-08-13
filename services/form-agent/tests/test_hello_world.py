"""Tests for the hello_world demonstration tool.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import importlib

import pytest

# tools.__init__ re-exports main as the name "hello_world", which shadows
# the submodule of the same name — import it directly to get the module.
hello_world = importlib.import_module("form_agent.tools.hello_world")


def test_simple_class_greet_logs_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """SimpleClass.greet() should log a greeting containing the instance name."""
    messages: list[str] = []
    monkeypatch.setattr(hello_world.logger, "info", lambda msg: messages.append(msg))

    hello_world.SimpleClass("World").greet()

    assert messages == ["Hello, World!"]


def test_main_configures_logging_and_greets_with_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() should configure logging, log system info, and greet using the configured LOG_LEVEL."""
    calls: list[str] = []

    class FakeConfig:
        LOG_LEVEL = "INFO"

    monkeypatch.setattr(hello_world, "configure_logger", lambda: calls.append("configure_logger"))
    monkeypatch.setattr(hello_world, "info_system", lambda: calls.append("info_system"))
    monkeypatch.setattr(hello_world, "get_env_config", lambda: FakeConfig())
    monkeypatch.setattr(hello_world.logger, "info", lambda msg: calls.append(msg))

    hello_world.main()

    assert calls == ["configure_logger", "info_system", "Hello, INFO!"]
