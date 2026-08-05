"""Tests for application startup behavior."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ValidationError

import etl_vectorize.app as app
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
        self.SCENARIOS_DIR = "/scenarios"
        self.SCENARIO_SLUG = "docs_rag"
        self.ORG_ID = "demo"
        self.MINIO_ENDPOINT = "http://minio:9000"
        self.MINIO_ACCESS_KEY = "ai_circus"
        self.MINIO_SECRET_KEY = FakeSecret("s3cret")
        self.QDRANT_URL = "http://qdrant:6333"


@dataclass
class FakeDocuments:
    """Stand-in for ai_circus_shared.scenario_schema.DocumentsConfig."""

    bucket: str = "scenario-docs-rag"
    embedding: object = None

    def __post_init__(self) -> None:
        """Attach a minimal fake embedding config."""

        @dataclass
        class FakeEmbedding:
            model: str = "fake-model"

        self.embedding = FakeEmbedding()


def build_validation_error() -> ValidationError:
    """Create a Pydantic validation error for testing startup failures."""

    class RequiredConfig(BaseModel):
        required_value: int

    try:
        RequiredConfig()
    except ValidationError as exc:
        return exc
    raise AssertionError("Expected ValidationError was not raised")


def test_main_runs_the_vectorize_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """app.main() loads the scenario, connects to MinIO/Qdrant/the embedding model, and runs the pipeline."""
    fake_logger = FakeLogger()
    fake_documents = FakeDocuments()
    fake_vector_store = object()

    class FakeDefinition:
        documents = fake_documents
        vector_store = fake_vector_store

    connect_calls: list[dict[str, object]] = []
    qdrant_calls: list[dict[str, object]] = []
    model_calls: list[str] = []
    vectorize_calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app.ScenarioDefinition, "load", staticmethod(lambda _path: FakeDefinition()))
    monkeypatch.setattr(
        app.ObjectStore,
        "connect",
        staticmethod(lambda **kwargs: connect_calls.append(kwargs) or "fake-store"),
    )
    monkeypatch.setattr(app, "QdrantClient", lambda **kwargs: qdrant_calls.append(kwargs) or "fake-qdrant")
    monkeypatch.setattr(app, "SentenceTransformer", lambda name: model_calls.append(name) or "fake-model-instance")
    monkeypatch.setattr(app, "run_vectorize", lambda *args: vectorize_calls.append(args))

    app.main()

    assert connect_calls == [
        {
            "bucket": "scenario-docs-rag",
            "endpoint_url": "http://minio:9000",
            "access_key": "ai_circus",
            "secret_key": "s3cret",
        }
    ]
    assert qdrant_calls == [{"url": "http://qdrant:6333"}]
    assert model_calls == ["fake-model"]
    expected_dir = app.Path("/scenarios/docs_rag")
    assert vectorize_calls == [
        ("fake-store", "fake-qdrant", "fake-model-instance", "demo", fake_documents, fake_vector_store, expected_dir)
    ]
    assert fake_logger.success_messages


def test_main_exits_if_scenario_has_no_documents_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scenario without documents/vector_store config (wrong kind) is rejected with a clear error."""
    fake_logger = FakeLogger()

    class FakeDefinitionWithoutDocuments:
        documents = None
        vector_store = None

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app.ScenarioDefinition, "load", staticmethod(lambda _path: FakeDefinitionWithoutDocuments()))

    with pytest.raises(SystemExit) as exc_info:
        app.main()

    assert exc_info.value.code == 1
    assert fake_logger.error_messages


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
