"""Tests for application startup behavior (main())."""

from __future__ import annotations

import asyncio
import types

import pytest
from pydantic import BaseModel, ValidationError

import agui_voice.app as app
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

    def exception(self, *args: object) -> None:
        """Map exception logging to the error collector for test assertions."""
        self.error(*args)


class FakeEnvConfig:
    """Minimal stand-in for the generated EnvConfig, covering the fields app.main() reads."""

    def __init__(self) -> None:
        """Populate fixed, valid-looking configuration values."""
        self.HTTP_PORT = "8000"
        self.LOG_LEVEL = "DEBUG"
        self.CORS_ALLOWED_ORIGINS = "http://react.localhost,http://localhost:5173"
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


def test_healthz() -> None:
    """GET /healthz returns a fixed liveness payload."""
    assert app.healthz() == {"status": "ok"}


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


async def test_prewarm_models_builds_vad_stt_and_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    """_prewarm_models calls all three factories (populating providers.py's caches
    as a side effect) once at startup, instead of waiting for the first real caller.
    """
    calls: list[str] = []
    fake_logger = FakeLogger()
    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "build_vad_analyzer", lambda: calls.append("vad") or object())
    monkeypatch.setattr(app, "build_stt_service", lambda _config: calls.append("stt") or object())
    monkeypatch.setattr(app, "build_tts_service", lambda _config: calls.append("tts") or (object(), {}))
    state = types.SimpleNamespace(models_warm=False)

    await app._prewarm_models(state)

    assert calls == ["vad", "stt", "tts"]
    assert fake_logger.success_messages
    assert state.models_warm is True


async def test_prewarm_models_logs_and_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misconfigured provider (e.g. a cloud TTS provider with no API key) logs and
    gives up quietly — the first real connection will surface the same error through
    its own request/response cycle instead of this background task crashing the app.
    """
    fake_logger = FakeLogger()
    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "build_vad_analyzer", lambda: object())

    def raise_value_error(_config: object) -> object:
        raise ValueError("ELEVENLABS_API_KEY must be set when TTS_PROVIDER=elevenlabs.")

    monkeypatch.setattr(app, "build_stt_service", raise_value_error)
    state = types.SimpleNamespace(models_warm=False)

    await app._prewarm_models(state)  # must not raise

    assert fake_logger.error_messages
    assert not fake_logger.success_messages
    # Ready anyway: a broken provider config must not keep the pod out of the Service forever.
    assert state.models_warm is True


async def test_lifespan_schedules_prewarming_in_the_background(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan starts pre-warming as a background task rather than awaiting it —
    /healthz must respond immediately, not only once warming finishes.
    """
    ran = asyncio.Event()

    async def fake_prewarm(_state: object) -> None:  # ruff: ignore[unused-async] (must match the real async _prewarm_models signature)
        ran.set()

    monkeypatch.setattr(app, "_prewarm_models", fake_prewarm)

    async with app.lifespan(app.app):
        # The lifespan body itself must not block on pre-warming finishing.
        assert not ran.is_set()

    await asyncio.wait_for(ran.wait(), timeout=1)


def test_readyz_holds_traffic_until_models_are_warm() -> None:
    """/healthz (liveness) is always 200; /readyz (readiness) is 503 until pre-warmed."""
    from fastapi.testclient import TestClient

    client = TestClient(app.app)  # no lifespan: state is set by hand below
    app.app.state.models_warm = False
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503
    app.app.state.models_warm = True
    assert client.get("/readyz").status_code == 200
