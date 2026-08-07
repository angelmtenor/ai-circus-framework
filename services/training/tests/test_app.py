"""Integration-style tests for the training job's main() entry point.

Runs the real sklearn/SHAP pipeline against a tiny synthetic dataset and a fake
in-memory object store — only get_env_config/resolve_scenarios/ObjectStore.connect
are faked, so this exercises the actual train -> select -> explain -> save pipeline.
"""

from __future__ import annotations

import io
import json

import joblib
import numpy as np
import pandas as pd
import pytest
from ai_circus_shared.scenario_schema import TabularDataset, TabularModel
from ai_circus_shared.tabular_ml import (
    MODEL_EXPLAINER_KEY,
    MODEL_METADATA_KEY,
    MODEL_PIPELINE_KEY,
    NORMALIZED_DATASET_KEY,
)
from pydantic import BaseModel, ValidationError

import training.app as app
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
        self.SCENARIOS = ""
        self.ORG_ID = "demo"
        self.MINIO_ENDPOINT = "http://minio:9000"
        self.MINIO_ACCESS_KEY = "ai_circus"
        self.MINIO_SECRET_KEY = FakeSecret("s3cret")


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore."""

    def __init__(self) -> None:
        """Start with an empty object map."""
        self.objects: dict[tuple[str, str], bytes] = {}

    def put(self, org_id: str, path: str, data: bytes) -> str:
        """Store bytes under a tenant-scoped path."""
        self.objects[org_id, path] = data
        return f"tenant-{org_id}/{path}"

    def get(self, org_id: str, path: str) -> bytes:
        """Retrieve previously stored bytes."""
        return self.objects[org_id, path]


def _synthetic_normalized_dataset() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = 120
    numeric_feature = rng.normal(size=n)
    category_feature = pd.Categorical(rng.choice(["A", "B"], size=n))
    target = (numeric_feature + rng.normal(scale=0.1, size=n) > 0).astype(int)
    return pd.DataFrame({"numeric_feature": numeric_feature, "category_feature": category_feature, "target": target})


def _synthetic_normalized_regression_dataset() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = 120
    numeric_feature = rng.normal(size=n)
    category_feature = pd.Categorical(rng.choice(["A", "B"], size=n))
    target = numeric_feature * 2 + rng.normal(scale=0.1, size=n)
    return pd.DataFrame({"numeric_feature": numeric_feature, "category_feature": category_feature, "target": target})


@pytest.fixture
def fake_definition() -> object:
    """A scenario definition with real Tabular{Dataset,Model} config over synthetic columns."""

    class FakeDefinition:
        dataset = TabularDataset(
            bucket="scenario-churn",
            raw_object="raw/x.csv",
            seed_file="sample_data/x.csv",
            index_col="id",
            target="target",
            feature_columns=["numeric_feature", "category_feature"],
            feature_schema={
                "numeric_feature": {"type": "numeric", "min": -3, "max": 3, "default": 0},
                "category_feature": {"type": "categorical", "options": ["A", "B"], "default": "A"},
            },
        )
        model = TabularModel(
            task_type="classification",
            candidates=["lightgbm"],
            accuracy_gain_threshold_for_complexity=0.02,
        )

    return FakeDefinition()


@pytest.fixture
def fake_regression_definition() -> object:
    """A regression scenario definition with real Tabular{Dataset,Model} config."""

    class FakeDefinition:
        dataset = TabularDataset(
            bucket="scenario-supply-chain",
            raw_object="raw/x.csv",
            seed_file="sample_data/x.csv",
            index_col="id",
            target="target",
            feature_columns=["numeric_feature", "category_feature"],
            feature_schema={
                "numeric_feature": {"type": "numeric", "min": -3, "max": 3, "default": 0},
                "category_feature": {"type": "categorical", "options": ["A", "B"], "default": "A"},
            },
        )
        model = TabularModel(
            task_type="regression",
            candidates=["lightgbm"],
            accuracy_gain_threshold_for_complexity=0.02,
            target_units="days",
        )

    return FakeDefinition()


def build_validation_error() -> ValidationError:
    """Create a Pydantic validation error for testing startup failures."""

    class RequiredConfig(BaseModel):
        required_value: int

    try:
        RequiredConfig()
    except ValidationError as exc:
        return exc
    raise AssertionError("Expected ValidationError was not raised")


def test_main_trains_selects_explains_and_saves(monkeypatch: pytest.MonkeyPatch, fake_definition: object) -> None:
    """main() runs the full pipeline and writes pipeline/explainer/metadata to the store."""
    fake_logger = FakeLogger()
    store = FakeObjectStore()
    df = _synthetic_normalized_dataset()
    buffer = io.BytesIO()
    df.to_parquet(buffer)
    store.put("demo", NORMALIZED_DATASET_KEY, buffer.getvalue())

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "resolve_scenarios", lambda *_a, **_kw: {"churn": fake_definition})
    monkeypatch.setattr(app.ObjectStore, "connect", staticmethod(lambda **_kwargs: store))

    app.main()

    assert ("demo", MODEL_PIPELINE_KEY) in store.objects
    assert ("demo", MODEL_EXPLAINER_KEY) in store.objects
    assert ("demo", MODEL_METADATA_KEY) in store.objects

    pipeline = joblib.load(io.BytesIO(store.objects["demo", MODEL_PIPELINE_KEY]))
    predictions = pipeline.predict(df[["numeric_feature", "category_feature"]])
    assert len(predictions) == len(df)

    metadata = json.loads(store.objects["demo", MODEL_METADATA_KEY])
    assert metadata["model_name"] == "lightgbm"
    assert metadata["candidates_evaluated"] == ["lightgbm"]
    assert metadata["task_type"] == "classification"
    assert fake_logger.success_messages


def test_main_trains_regression_scenario_without_stratify(
    monkeypatch: pytest.MonkeyPatch, fake_regression_definition: object
) -> None:
    """A regression scenario (continuous target) trains successfully without stratify=y."""
    fake_logger = FakeLogger()
    store = FakeObjectStore()
    df = _synthetic_normalized_regression_dataset()
    buffer = io.BytesIO()
    df.to_parquet(buffer)
    store.put("demo", NORMALIZED_DATASET_KEY, buffer.getvalue())

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "resolve_scenarios", lambda *_a, **_kw: {"supply_chain": fake_regression_definition})
    monkeypatch.setattr(app.ObjectStore, "connect", staticmethod(lambda **_kwargs: store))

    app.main()

    pipeline = joblib.load(io.BytesIO(store.objects["demo", MODEL_PIPELINE_KEY]))
    predictions = pipeline.predict(df[["numeric_feature", "category_feature"]])
    assert len(predictions) == len(df)

    metadata = json.loads(store.objects["demo", MODEL_METADATA_KEY])
    assert metadata["model_name"] == "lightgbm"
    assert metadata["task_type"] == "regression"
    assert fake_logger.success_messages


def test_main_exits_if_no_scenario_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty SCENARIOS resolution (no tabular_ml scenarios found at all) is rejected with a clear error."""
    fake_logger = FakeLogger()

    monkeypatch.setattr(app, "logger", fake_logger)
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(app, "resolve_scenarios", lambda *_a, **_kw: {})

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
