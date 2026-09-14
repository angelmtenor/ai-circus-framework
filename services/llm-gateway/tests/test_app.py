"""Tests for application startup behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

import llm_gateway.app as app
from tests.conftest import FakeSecret


class FakeLogger:
    """Minimal logger used to capture app log calls."""

    def __init__(self) -> None:
        """Initialize in-memory message collectors used by tests."""
        self.success_messages: list[tuple[object, ...]] = []
        self.error_messages: list[tuple[object, ...]] = []

    def success(self, *args: object) -> None:
        """Record success log calls."""
        self.success_messages.append(args)

    def error(self, *args: object) -> None:
        """Record error log calls."""
        self.error_messages.append(args)


class FakeEnvConfig:
    """Minimal stand-in for the generated EnvConfig, covering the fields app.main() reads."""

    def __init__(self) -> None:
        """Populate fixed, valid-looking configuration values."""
        self.HTTP_PORT = "4000"
        self.LITELLM_CONFIG_PATH = "litellm_config.yaml"
        self.LITELLM_MASTER_KEY = FakeSecret("master-key")


def build_validation_error() -> ValidationError:
    """Create a Pydantic validation error for testing startup failures."""

    class RequiredConfig(BaseModel):
        required_value: int

    try:
        RequiredConfig()
    except ValidationError as exc:
        return exc
    raise AssertionError("Expected ValidationError was not raised")


def test_main_execs_litellm_with_resolved_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """app.main validates config, then hands off to launch() with the right argv/env."""
    fake_logger = FakeLogger()
    launch_calls: list[tuple[list[str], dict[str, str]]] = []

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "launch", lambda argv, env: launch_calls.append((argv, env)))

    app.main()

    assert len(launch_calls) == 1
    argv, env = launch_calls[0]
    assert argv[0] == "litellm"
    assert argv[argv.index("--config") + 1].endswith("litellm_config.yaml")
    assert argv[argv.index("--port") + 1] == "4000"
    assert env["LITELLM_MASTER_KEY"] == "master-key"
    assert fake_logger.success_messages


def test_main_exits_on_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that app.main exits with code 1 when config validation fails."""
    fake_logger = FakeLogger()
    validation_error = build_validation_error()

    def raise_validation_error() -> object:
        raise validation_error

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", raise_validation_error)

    with pytest.raises(SystemExit) as exc_info:
        app.main()

    assert exc_info.value.code == 1
    assert fake_logger.error_messages


def test_resolve_config_path_is_a_noop_without_langfuse_keys(tmp_path: Path) -> None:
    """Without both Langfuse keys the committed config is used verbatim (no console exporter)."""
    config = tmp_path / "litellm_config.yaml"
    config.write_text("litellm_settings:\n  callbacks: [llm_gateway.budget_hook.instance]\n")

    assert app.resolve_config_path(config, {"LANGFUSE_PUBLIC_KEY": "pk"}) == config
    assert app.resolve_config_path(config, {}) == config


def test_resolve_config_path_appends_langfuse_callback(tmp_path: Path) -> None:
    """With both keys set, a merged copy gains `langfuse_otel` next to the existing callbacks."""
    config = tmp_path / "litellm_config.yaml"
    config.write_text(
        "model_list:\n  - model_name: m\n    litellm_params: {model: openai/m}\n"
        "litellm_settings:\n  callbacks: [llm_gateway.budget_hook.instance]\n"
    )

    merged = app.resolve_config_path(config, {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"})

    assert merged != config
    loaded = yaml.safe_load(merged.read_text())
    assert loaded["litellm_settings"]["callbacks"] == ["llm_gateway.budget_hook.instance", "langfuse_otel"]
    assert loaded["model_list"][0]["model_name"] == "m"  # everything else untouched
    assert yaml.safe_load(config.read_text())["litellm_settings"]["callbacks"] == ["llm_gateway.budget_hook.instance"]
