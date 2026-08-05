"""Tests for application startup behavior."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ValidationError

import etl_tabular.app as app
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
        self.SCENARIO_SLUG = "churn"
        self.ORG_ID = "demo"
        self.MINIO_ENDPOINT = "http://minio:9000"
        self.MINIO_ACCESS_KEY = "ai_circus"
        self.MINIO_SECRET_KEY = FakeSecret("s3cret")


@dataclass
class FakeDataset:
    """Stand-in for ai_circus_shared.scenario_schema.TabularDataset."""

    bucket: str = "scenario-churn"


def build_validation_error() -> ValidationError:
    """Create a Pydantic validation error for testing startup failures."""

    class RequiredConfig(BaseModel):
        required_value: int

    try:
        RequiredConfig()
    except ValidationError as exc:
        return exc
    raise AssertionError("Expected ValidationError was not raised")


def test_main_runs_the_etl_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """app.main() loads the scenario, connects to MinIO, and runs the ETL pipeline."""
    fake_logger = FakeLogger()
    fake_dataset = FakeDataset()

    class FakeDefinition:
        dataset = fake_dataset

    connect_calls: list[dict[str, object]] = []
    etl_calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app.ScenarioDefinition, "load", staticmethod(lambda _path: FakeDefinition()))
    monkeypatch.setattr(
        app.ObjectStore,
        "connect",
        staticmethod(lambda **kwargs: connect_calls.append(kwargs) or "fake-store"),
    )
    monkeypatch.setattr(app, "run_etl", lambda *args: etl_calls.append(args))

    app.main()

    assert connect_calls == [
        {
            "bucket": "scenario-churn",
            "endpoint_url": "http://minio:9000",
            "access_key": "ai_circus",
            "secret_key": "s3cret",
        }
    ]
    assert etl_calls == [("fake-store", "demo", fake_dataset, app.Path("/scenarios/churn"))]
    assert fake_logger.success_messages


def test_main_exits_if_scenario_has_no_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scenario without dataset config (wrong kind) is rejected with a clear error."""
    fake_logger = FakeLogger()

    class FakeDefinitionWithoutDataset:
        dataset = None

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app.ScenarioDefinition, "load", staticmethod(lambda _path: FakeDefinitionWithoutDataset()))

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
