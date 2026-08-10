"""Tests for application startup behavior."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

import platform_registry.app as app
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
    """Minimal stand-in for the generated EnvConfig, used by app.main() tests."""

    def __init__(self, admin_api_key: str = "test-admin-key") -> None:
        """Populate just the fields app.main() reads."""
        self.HTTP_PORT = "8000"
        self.LOG_LEVEL = "DEBUG"
        self.POSTGRES_PASSWORD = FakeSecret()
        self.CORS_ALLOWED_ORIGINS = "http://react.localhost,http://localhost:5173"
        self.ADMIN_API_KEY = FakeSecret(admin_api_key)


def build_validation_error() -> ValidationError:
    """Create a Pydantic validation error for testing startup failures."""

    class RequiredConfig(BaseModel):
        required_value: int

    try:
        RequiredConfig()
    except ValidationError as exc:
        return exc
    raise AssertionError("Expected ValidationError was not raised")


def test_main_runs_and_starts_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that app.main loads config, logs success, and hands off to uvicorn.run."""
    fake_logger = FakeLogger()
    fake_env = FakeEnvConfig()
    uvicorn_calls: list[dict[str, object]] = []

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: fake_env)
    monkeypatch.setattr(app.uvicorn, "run", lambda *_args, **kwargs: uvicorn_calls.append(kwargs))

    app.main()

    assert fake_logger.success_messages
    assert uvicorn_calls == [{"host": "0.0.0.0", "port": 8000, "log_level": "debug"}]  # ruff: ignore[hardcoded-bind-all-interfaces]


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


def test_main_exits_when_demo_admin_key_used_outside_dev_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Booting with the shipped demo ADMIN_API_KEY under a staging/production
    profile is refused, not silently allowed.
    """
    fake_logger = FakeLogger()

    monkeypatch.setenv("APP_ENVIRONMENT", "staging")
    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig(admin_api_key="ai-circus-2026"))

    with pytest.raises(SystemExit) as exc_info:
        app.main()

    assert exc_info.value.code == 1
    assert fake_logger.error_messages


@pytest.mark.parametrize("profile", ["local", "docker"])
def test_main_allows_demo_admin_key_under_dev_profiles(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """The shipped demo ADMIN_API_KEY is fine under the local/docker dev profiles."""
    fake_logger = FakeLogger()
    uvicorn_calls: list[dict[str, object]] = []

    monkeypatch.setenv("APP_ENVIRONMENT", profile)
    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig(admin_api_key="ai-circus-2026"))
    monkeypatch.setattr(app.uvicorn, "run", lambda *_args, **kwargs: uvicorn_calls.append(kwargs))

    app.main()

    assert uvicorn_calls
