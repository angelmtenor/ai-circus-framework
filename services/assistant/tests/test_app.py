"""Tests for application startup behavior (main()) and the lifespan handler."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

import assistant.app as app
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
        self.HTTP_PORT = "8000"
        self.LOG_LEVEL = "DEBUG"
        self.SCENARIO_SLUG = "churn"
        self.SCENARIOS_DIR = "/scenarios"
        self.MINIO_ENDPOINT = "http://minio:9000"
        self.MINIO_ACCESS_KEY = "ai_circus"
        self.MINIO_SECRET_KEY = FakeSecret("s3cret")
        self.LLM_GATEWAY_URL = "http://llm-gateway:4000"
        self.LLM_GATEWAY_API_KEY = FakeSecret("master-key")


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
    uvicorn_calls: list[dict[str, object]] = []

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
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


async def test_lifespan_sets_up_prompt_cache_and_llm_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan handler resolves the scenario's bucket and stashes cache+client on app.state."""

    class FakeDataset:
        bucket = "scenario-churn"

    class FakeDefinition:
        dataset = FakeDataset()

    connect_calls: list[dict[str, object]] = []

    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app.ScenarioDefinition, "load", staticmethod(lambda _path: FakeDefinition()))
    monkeypatch.setattr(
        app.ObjectStore, "connect", staticmethod(lambda **kwargs: connect_calls.append(kwargs) or "fake-store")
    )

    async with app.lifespan(app.app):
        assert app.app.state.prompt_cache._store == "fake-store"
        assert str(app.app.state.llm_client.base_url) == "http://llm-gateway:4000"

    assert connect_calls == [
        {
            "bucket": "scenario-churn",
            "endpoint_url": "http://minio:9000",
            "access_key": "ai_circus",
            "secret_key": "s3cret",
        }
    ]


async def test_lifespan_rejects_scenario_without_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scenario with no dataset config (wrong kind) fails startup loudly, not silently."""

    class FakeDefinitionWithoutDataset:
        dataset = None

    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app.ScenarioDefinition, "load", staticmethod(lambda _path: FakeDefinitionWithoutDataset()))

    with pytest.raises(RuntimeError, match="no dataset config"):
        async with app.lifespan(app.app):
            pass
