"""GET /model/{slug}/drift — the caller's own drift, fed by its /predict traffic."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest
from ai_circus_shared.auth import Identity
from ai_circus_shared.drift import PSI_SIGNIFICANT, build_reference
from fastapi import FastAPI
from fastapi.testclient import TestClient

import prediction.api as api
from prediction.api import _model_cache, _scenario_definition, router
from prediction.core.drift_monitor import DriftMonitor
from prediction.core.identity import resolve_identity
from prediction.core.model_cache import ModelArtifacts, ModelCache

RNG = np.random.default_rng(11)
TRAIN_SCORES = [float(v) for v in RNG.normal(650, 60, 2_000)]
METADATA = {
    "feature_columns": ["CreditScore", "Geography"],
    "transformed_feature_names": ["f1", "f2"],
    "task_type": "classification",
    "model_name": "lightgbm",
    "checksums": {"model.joblib": "aa"},
    "drift_reference": build_reference(
        {"CreditScore": TRAIN_SCORES, "Geography": [str(v) for v in RNG.choice(["France", "Spain"], 2_000)]},
        {"CreditScore": "numeric", "Geography": "categorical"},
        ["CreditScore", "Geography"],
        [0.6] * 2_000,
    ),
}


class _Pipeline:
    class _Pre:
        @staticmethod
        def transform(x: object) -> object:
            return x

    named_steps: ClassVar = {"preprocessor": _Pre()}

    @staticmethod
    def predict_proba(x: object) -> np.ndarray:
        return np.array([[0.4, 0.6] for _ in range(len(x))])


@contextmanager
def _client(org_id: str, metadata: dict | None = None) -> Generator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    artifacts = ModelArtifacts(pipeline=_Pipeline(), explainer=None, metadata=dict(metadata or METADATA))

    class FakeCache(ModelCache):
        def __init__(self) -> None:
            pass

        def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
            return artifacts

    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id=org_id, roles=frozenset({"scenario:churn"})
    )
    app.dependency_overrides[_scenario_definition] = lambda: SimpleNamespace(
        slug="churn",
        governance=None,
        dataset=SimpleNamespace(text_columns=list, feature_schema={}, business_rules=None),
        model=SimpleNamespace(decision_policy=None),
    )
    cache = FakeCache()
    app.dependency_overrides[_model_cache] = lambda: cache
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def fresh_monitor(monkeypatch: pytest.MonkeyPatch) -> DriftMonitor:
    monitor = DriftMonitor()
    monkeypatch.setattr(api, "DRIFT_MONITOR", monitor)
    return monitor


def _shifted_records(n: int) -> list[dict]:
    return [{"CreditScore": float(v), "Geography": "France"} for v in RNG.normal(820, 30, n)]


def test_the_caller_sees_its_own_drift_after_scoring() -> None:
    with _client("org-1") as client:
        assert (
            client.post("/predict/churn", json={"records": _shifted_records(150), "explain": False}).status_code == 200
        )
        body = client.get("/model/churn/drift").json()
    assert body["available"] is True
    assert body["window_rows"] == 150
    assert body["features"][0]["feature"] == "CreditScore"
    assert body["features"][0]["psi"] > PSI_SIGNIFICANT
    assert body["thresholds"]["significant"] == PSI_SIGNIFICANT


def test_another_tenant_on_the_same_model_sees_none_of_it() -> None:
    with _client("org-1") as client:
        client.post("/predict/churn", json={"records": _shifted_records(150), "explain": False})
    with _client("org-2") as other:
        body = other.get("/model/churn/drift").json()
    assert body["window_rows"] == 0
    assert body["features"] == [] and body["score_psi"] is None


def test_a_model_without_a_reference_says_so() -> None:
    legacy = {k: v for k, v in METADATA.items() if k != "drift_reference"}
    with _client("org-1", legacy) as client:
        body = client.get("/model/churn/drift").json()
    assert body["available"] is False
