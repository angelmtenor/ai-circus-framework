"""Tests for application startup behavior."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

import ui_streamlit.app as app


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
        self.HTTP_PORT = "8501"


def build_validation_error() -> ValidationError:
    """Create a Pydantic validation error for testing startup failures."""

    class RequiredConfig(BaseModel):
        required_value: int

    try:
        RequiredConfig()
    except ValidationError as exc:
        return exc
    raise AssertionError("Expected ValidationError was not raised")


def test_main_execs_streamlit_against_the_ui_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """app.main validates config, then hands off to launch() with the right streamlit argv."""
    fake_logger = FakeLogger()
    launch_calls: list[tuple[list[str], dict[str, str]]] = []

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "launch", lambda argv, env: launch_calls.append((argv, env)))

    app.main()

    assert len(launch_calls) == 1
    argv, _env = launch_calls[0]
    assert argv[0] == "streamlit"
    assert argv[1] == "run"
    assert argv[2].endswith("streamlit_app.py")
    assert argv[argv.index("--server.port") + 1] == "8501"
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
