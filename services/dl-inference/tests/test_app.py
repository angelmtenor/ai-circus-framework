"""Tests for application startup behavior (main()) and the lifespan handler."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from ai_circus_shared.auth import Identity
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

import dl_inference.app as app
from dl_inference.api import _cache, _definition
from dl_inference.core.identity import resolve_identity
from dl_inference.core.model_cache import ModelNotTrainedError, ModelUnavailableError
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
        self.OBJECT_STORE_ENDPOINT = "http://seaweedfs:8333"
        self.OBJECT_STORE_ACCESS_KEY = "ai_circus"
        self.OBJECT_STORE_SECRET_KEY = FakeSecret("s3cret")
        self.CORS_ALLOWED_ORIGINS = "http://react.localhost,http://localhost:5173"
        self.SHARED_MODEL_ORG_ID = "demo"
        self.AUTH_DISABLED = "false"
        self.ADMIN_API_KEY = FakeSecret("admin-key")
        self.ENGINEERING_DEMO_API_KEY = None
        self.ORT_THREADS = "2"


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


async def test_lifespan_sets_up_model_cache_from_resolved_scenarios(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan handler connects a store per resolved scenario and stashes a DlModelCache on app.state."""

    class FakeDeepLearning:
        bucket = "scenario-symptom-triage"

    class FakeDefinition:
        deep_learning = FakeDeepLearning()

    connect_calls: list[dict[str, object]] = []

    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "resolve_scenarios", lambda *_a, **_kw: {"symptom_triage": FakeDefinition()})
    monkeypatch.setattr(
        app.ObjectStore, "connect", staticmethod(lambda **kwargs: connect_calls.append(kwargs) or "fake-store")
    )

    async with app.lifespan(app.app):
        assert app.app.state.model_cache._stores == {"symptom_triage": "fake-store"}
        assert app.app.state.model_cache._threads == 2
        assert set(app.app.state.definitions) == {"symptom_triage"}

    assert connect_calls == [
        {
            "bucket": "scenario-symptom-triage",
            "endpoint_url": "http://seaweedfs:8333",
            "access_key": "ai_circus",
            "secret_key": "s3cret",
        }
    ]


def test_model_unavailable_error_gets_503_with_cors_headers_not_a_bare_500() -> None:
    """A missing-model-artifacts error must come back as a clean 503 *with* CORS
    headers — not an unhandled 500. Starlette's ServerErrorMiddleware (which builds
    the default 500 response for any exception with no registered handler) sits
    *outside* CORSMiddleware, so that default response carries no CORS headers at
    all — the browser then reports the whole request as a generic "Failed to fetch"
    instead of a readable error. Registering `_model_unavailable_handler` for
    ModelUnavailableError keeps the response inside CORSMiddleware's wrapped app.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    from dl_inference.api import router

    class FailingModelCache:
        def get(self, org_id: str, scenario_slug: str) -> None:
            raise ModelNotTrainedError(f"No trained deep-learning model for scenario={scenario_slug!r}.")

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.add_exception_handler(ModelUnavailableError, app._model_unavailable_handler)
    test_app.add_middleware(
        CORSMiddleware, allow_origins=["http://react.localhost"], allow_methods=["*"], allow_headers=["*"]
    )
    test_app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset()
    )
    test_app.dependency_overrides[_definition] = lambda: SimpleNamespace(slug="symptom_triage")
    test_app.dependency_overrides[_cache] = lambda: FailingModelCache()

    response = TestClient(test_app).post(
        "/predict/symptom_triage", json={"text": "fever"}, headers={"Origin": "http://react.localhost"}
    )

    assert response.status_code == 503
    assert response.headers["access-control-allow-origin"] == "http://react.localhost"
    assert "No trained deep-learning model" in response.json()["detail"]


async def test_lifespan_rejects_when_no_scenario_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty SCENARIOS resolution (no deep_learning scenarios found at all) fails startup loudly."""
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "resolve_scenarios", lambda *_a, **_kw: {})

    with pytest.raises(RuntimeError, match="No deep_learning scenario matched"):
        async with app.lifespan(app.app):
            pass
