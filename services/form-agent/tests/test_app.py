"""Tests for application startup behavior (main()) and the lifespan handler."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

import form_agent.app as app
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
        self.LLM_MODEL = "gpt-4o-mini"
        self.QDRANT_URL = "http://qdrant:6333"
        self.MINIO_ENDPOINT = "http://minio:9000"
        self.MINIO_ACCESS_KEY = "ai_circus"
        self.MINIO_SECRET_KEY = FakeSecret("minio-secret")
        self.LLM_GATEWAY_URL = "http://llm-gateway:4000"
        self.LLM_GATEWAY_API_KEY = FakeSecret("master-key")
        self.CORS_ALLOWED_ORIGINS = "http://react.localhost,http://localhost:5173"
        self.EMBEDDING_PROVIDER = "local"
        self.EMBEDDING_MODEL = None
        self.GOOGLE_API_KEY = None
        self.VOYAGE_API_KEY = None


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


async def test_lifespan_sets_up_qdrant_embedder_store_and_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan handler resolves scenarios and stashes clients/embedder/store on app.state."""

    class FakeDefinition:
        pass

    qdrant_calls: list[dict[str, object]] = []
    provider_calls: list[dict[str, object]] = []
    store_calls: list[dict[str, object]] = []

    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "resolve_scenarios", lambda *_a, **_kw: {"service_request": FakeDefinition()})
    monkeypatch.setattr(app, "QdrantClient", lambda **kwargs: qdrant_calls.append(kwargs) or "fake-qdrant")
    monkeypatch.setattr(
        app, "build_embedding_provider", lambda **kwargs: provider_calls.append(kwargs) or "fake-embedder"
    )
    monkeypatch.setattr(
        app.ObjectStore,
        "connect",
        classmethod(lambda _cls, **kwargs: store_calls.append(kwargs) or "fake-store"),
    )

    async with app.lifespan(app.app):
        assert app.app.state.qdrant == "fake-qdrant"
        assert app.app.state.embedder == "fake-embedder"
        assert app.app.state.store == "fake-store"
        assert set(app.app.state.definitions) == {"service_request"}
        # No client built eagerly here anymore — api._llm() builds one per model_name,
        # lazily, once it knows (from platform-registry) which model is actually active.
        assert app.app.state.llm_clients == {}

    assert qdrant_calls == [{"url": "http://qdrant:6333"}]
    assert provider_calls == [{"provider": "local", "model_name": None, "google_api_key": None, "voyage_api_key": None}]
    assert store_calls == [
        {
            "bucket": "form-agent-submissions",
            "endpoint_url": "http://minio:9000",
            "access_key": "ai_circus",
            "secret_key": "minio-secret",
        }
    ]


async def test_lifespan_rejects_when_no_scenario_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty SCENARIOS resolution (no assisted_form scenarios found at all) fails startup loudly."""
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "resolve_scenarios", lambda *_a, **_kw: {})

    with pytest.raises(RuntimeError, match="No assisted_form scenario matched"):
        async with app.lifespan(app.app):
            pass
