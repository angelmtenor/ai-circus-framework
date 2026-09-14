"""Tests for the MLflow run mirror (training.core.mlflow_tracking)."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest

from training.core import mlflow_tracking


@dataclass(frozen=True)
class Candidate:
    """The (name, test_score) shape of training.core.training.TrainedCandidate."""

    name: str
    test_score: float


class FakeMlflow(types.ModuleType):
    """Records every call training.core.mlflow_tracking makes against the `mlflow` module."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        super().__init__("mlflow")
        self.tracking_uri: str | None = None
        self.experiment: str | None = None
        self.runs: list[dict[str, Any]] = []
        self.params: dict[str, Any] = {}
        self.metrics: dict[str, float] = {}
        self.dicts: dict[str, dict[str, Any]] = {}

    def set_tracking_uri(self, uri: str) -> None:
        """Record the tracker URI."""
        self.tracking_uri = uri

    def set_experiment(self, name: str) -> None:
        """Record the experiment name."""
        self.experiment = name

    @contextmanager
    def start_run(self, **kwargs: Any) -> Iterator[None]:
        """Record the run's kwargs (run_name/tags) and yield like the real context manager."""
        self.runs.append(kwargs)
        yield

    def log_params(self, params: dict[str, Any]) -> None:
        """Record params."""
        self.params.update(params)

    def log_metric(self, key: str, value: float) -> None:
        """Record one metric."""
        self.metrics[key] = value

    def log_dict(self, payload: dict[str, Any], artifact_file: str) -> None:
        """Record a dict artifact."""
        self.dicts[artifact_file] = payload


METADATA = {
    "task_type": "classification",
    "has_intervals": False,
    "feature_columns": ["a", "b"],
    "target": "churn",
    "checksums": {"pipeline": "abc", "explainer": "def"},
}
CANDIDATES = [Candidate("logistic_regression", 0.81), Candidate("lightgbm", 0.84)]


def _log(uri: str | None) -> None:
    mlflow_tracking.log_training_run(
        uri,
        org_id="demo",
        scenario_slug="churn",
        candidates=CANDIDATES,
        selected=CANDIDATES[1],
        metadata=METADATA,
        dataset_rows=1234,
        accuracy_gain_threshold=0.02,
    )


def test_unset_tracking_uri_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """No MLFLOW_TRACKING_URI -> mlflow is never even imported."""
    monkeypatch.setitem(sys.modules, "mlflow", None)  # importing would raise ImportError
    _log(None)
    _log("")


def test_logs_one_run_with_candidates_selection_and_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """A set URI records experiment, tags, per-candidate scores and metadata.json."""
    fake = FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake)

    _log("http://mlflow:5000")

    assert fake.tracking_uri == "http://mlflow:5000"
    assert fake.experiment == "scenario/churn"
    assert len(fake.runs) == 1
    assert fake.runs[0]["tags"]["org_id"] == "demo"
    assert fake.runs[0]["tags"]["selected_model"] == "lightgbm"
    assert fake.params["candidates"] == "logistic_regression,lightgbm"
    assert fake.params["checksum.pipeline"] == "abc"
    assert fake.params["dataset_rows"] == 1234
    assert fake.metrics == {"test_score.logistic_regression": 0.81, "test_score.lightgbm": 0.84, "test_score": 0.84}
    assert fake.dicts["metadata.json"] is METADATA


def test_tracker_errors_never_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable tracker is logged and swallowed — training artifacts are already saved."""
    fake = FakeMlflow()

    def boom(_uri: str) -> None:
        raise ConnectionError("mlflow down")

    fake.set_tracking_uri = boom  # type: ignore[method-assign]
    monkeypatch.setitem(sys.modules, "mlflow", fake)

    _log("http://mlflow:5000")  # must not raise
    assert fake.runs == []
