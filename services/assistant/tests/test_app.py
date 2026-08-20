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
        self.SCENARIOS = ""
        self.SCENARIOS_DIR = "/scenarios"
        self.MINIO_ENDPOINT = "http://minio:9000"
        self.MINIO_ACCESS_KEY = "ai_circus"
        self.MINIO_SECRET_KEY = FakeSecret("s3cret")
        self.LLM_GATEWAY_URL = "http://llm-gateway:4000"
        self.LLM_GATEWAY_API_KEY = FakeSecret("master-key")
        self.CORS_ALLOWED_ORIGINS = "http://react.localhost,http://localhost:5173"
        self.SHARED_MODEL_ORG_ID = "demo"
        self.AUTH_DISABLED = "false"
        self.ADMIN_API_KEY = FakeSecret("admin-key")
        self.ENGINEERING_DEMO_API_KEY = None


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


async def test_lifespan_sets_up_prompt_cache_and_chat_llm_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan handler connects a store per resolved scenario and stashes cache+client dict on app.state."""

    class FakeDataset:
        bucket = "scenario-churn"

    class FakeDefinition:
        dataset = FakeDataset()

    connect_calls: list[dict[str, object]] = []

    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "resolve_scenarios", lambda *_a, **_kw: {"churn": FakeDefinition()})
    monkeypatch.setattr(
        app.ObjectStore, "connect", staticmethod(lambda **kwargs: connect_calls.append(kwargs) or "fake-store")
    )

    async with app.lifespan(app.app):
        assert app.app.state.prompt_cache._stores == {"churn": "fake-store"}
        # Empty until the AG-UI route's _chat_llm dependency lazily builds+caches a
        # ChatOpenAI client for whichever model_name the first request resolves to.
        assert app.app.state.chat_llm_clients == {}

    assert connect_calls == [
        {
            "bucket": "scenario-churn",
            "endpoint_url": "http://minio:9000",
            "access_key": "ai_circus",
            "secret_key": "s3cret",
        }
    ]


async def test_lifespan_rejects_when_no_scenario_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty SCENARIOS resolution (no tabular_ml scenarios found at all) fails startup loudly."""
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "resolve_scenarios", lambda *_a, **_kw: {})

    with pytest.raises(RuntimeError, match="No tabular_ml scenario matched"):
        async with app.lifespan(app.app):
            pass
