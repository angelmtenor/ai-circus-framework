"""Tests for the decision record each /predict request leaves (docs/governance/)."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, ClassVar

import numpy as np
import pytest
from ai_circus_shared.auth import Identity
from ai_circus_shared.decision_records import PredictionRecorder
from ai_circus_shared.governance_events import GovernanceEvents, GovernanceEventSettings
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prediction.api import _model_cache, _scenario_definition, router
from prediction.core.identity import resolve_identity
from prediction.core.model_cache import ModelArtifacts, ModelCache

TRACE = "0af7651916cd43dd8448eb211c80319c"


class _Pipeline:
    class _Pre:
        @staticmethod
        def transform(x: object) -> object:
            return x

    named_steps: ClassVar = {"preprocessor": _Pre()}

    @staticmethod
    def predict_proba(x: object) -> np.ndarray:
        """P(1) = 0.6 for every record."""
        return np.array([[0.4, 0.6] for _ in range(len(x))])


class _Explainer:
    @staticmethod
    def shap_values(x: object, *, check_additivity: bool = True) -> np.ndarray:
        """Fixed contributions."""
        return np.array([[0.1, -0.2] for _ in range(len(x))])


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def send(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _Store:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put(self, tenant_org_id: str, path: str, data: bytes) -> str:
        self.objects[tenant_org_id, path] = data
        return path

    def list(self, tenant_org_id: str, prefix: str = "") -> list[str]:
        return [p for (o, p) in self.objects if o == tenant_org_id and p.startswith(prefix)]

    def delete(self, tenant_org_id: str, path: str) -> None:
        self.objects.pop((tenant_org_id, path), None)


METADATA = {
    "feature_columns": ["CreditScore", "Geography"],
    "transformed_feature_names": ["f1", "f2"],
    "task_type": "classification",
    "model_name": "lightgbm",
    "checksums": {"model.joblib": "aa", "metadata.json": "bb"},
}


def _definition(policy: object = None, slug: str = "churn", governance: object = None) -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug,
        governance=governance,
        dataset=SimpleNamespace(text_columns=list, feature_schema={}, business_rules=None),
        model=SimpleNamespace(decision_policy=policy),
    )


@contextmanager
def _client(*, recorder: PredictionRecorder | None, definition: SimpleNamespace | None = None) -> Generator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    if recorder is not None:
        app.state.recorder = recorder
    artifacts = ModelArtifacts(pipeline=_Pipeline(), explainer=_Explainer(), metadata=dict(METADATA))

    class FakeCache(ModelCache):
        def __init__(self) -> None:
            pass

        def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
            return artifacts

    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})
    )
    app.dependency_overrides[_scenario_definition] = lambda: definition or _definition()
    cache = FakeCache()
    app.dependency_overrides[_model_cache] = lambda: cache
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def rig() -> Generator[tuple[TestClient, _Sink, _Store]]:
    sink, store = _Sink(), _Store()
    recorder = PredictionRecorder(
        events=GovernanceEvents(GovernanceEventSettings(enabled=True, source="/prediction"), sink=sink),
        stores={"churn": store},
    )
    with _client(recorder=recorder) as client:
        yield client, sink, store


RECORDS = [{"CreditScore": 700, "Geography": "France"}, {"CreditScore": 550, "Geography": "Spain"}]


def test_a_request_leaves_one_content_free_record(rig: tuple[TestClient, _Sink, _Store]) -> None:
    client, sink, store = rig
    response = client.post(
        "/predict/churn", json={"records": RECORDS}, headers={"traceparent": f"00-{TRACE}-b7ad6b7169203331-01"}
    )
    assert response.status_code == 200

    (event,) = sink.events
    assert event["type"] == "dev.aicircus.governance.prediction.recorded.v1"
    assert event["tenantid"] == "org-1" and event["aisystemid"] == "churn"
    data = event["data"]
    assert data["scenario_slug"] == "churn" and data["org_id"] == "org-1"
    assert data["model_name"] == "lightgbm" and data["model_variant"] == "primary"
    assert len(data["model_version"]) == 12
    assert data["prediction_count"] == 2 and data["outcomes"] == {"scored": 2}
    assert data["score_min"] == pytest.approx(0.6) and data["score_max"] == pytest.approx(0.6)
    assert data["explanation_available"] is True
    assert data["trace_id"] == TRACE, "the caller's W3C trace id ties the record to the request"

    ((org, path),) = store.objects
    assert org == "org-1" and path.startswith("governance/decision-records/") and TRACE in path

    # Nothing about the inputs or the explanation leaves the service.
    everything = json.dumps(event) + store.objects[org, path].decode()
    for leak in ("France", "Spain", "CreditScore", "700", "contributions", "f1"):
        assert leak not in everything


def test_the_explain_flag_off_is_recorded_as_no_explanation(rig: tuple[TestClient, _Sink, _Store]) -> None:
    client, sink, _ = rig
    client.post("/predict/churn", json={"records": RECORDS, "explain": False})
    assert sink.events[0]["data"]["explanation_available"] is False


def test_decision_policy_outcomes_are_counted_per_decision() -> None:
    sink = _Sink()
    recorder = PredictionRecorder(events=GovernanceEvents(GovernanceEventSettings(enabled=True), sink=sink))
    from ai_circus_shared.business_rules import DecisionPolicy

    policy = DecisionPolicy(
        approve_at=0.5, deny_at=0.2, approve_label="grant", deny_label="deny", review_label="review"
    )
    with _client(recorder=recorder, definition=_definition(policy)) as client:
        client.post("/predict/churn", json={"records": RECORDS})
    assert sink.events[0]["data"]["outcomes"] == {"approve": 2}


def test_a_declared_ai_system_id_is_used() -> None:
    from ai_circus_shared.governance import GovernanceConfig

    sink = _Sink()
    recorder = PredictionRecorder(events=GovernanceEvents(GovernanceEventSettings(enabled=True), sink=sink))
    declared = _definition(governance=GovernanceConfig(ai_system_id="churn-v2"))
    with _client(recorder=recorder, definition=declared) as client:
        client.post("/predict/churn", json={"records": RECORDS})
    assert sink.events[0]["data"]["ai_system_id"] == "churn-v2" and sink.events[0]["aisystemid"] == "churn-v2"


def test_with_no_recorder_or_everything_off_prediction_behaves_as_before() -> None:
    off = PredictionRecorder(events=GovernanceEvents(GovernanceEventSettings(enabled=False)))
    for recorder in (None, off):
        with _client(recorder=recorder) as client:
            response = client.post("/predict/churn", json={"records": RECORDS})
        assert response.status_code == 200 and len(response.json()["predictions"]) == 2


def test_a_broken_sink_or_store_never_fails_the_prediction() -> None:
    class Broken:
        def send(self, event: dict) -> None:
            raise RuntimeError("down")

    class BrokenStore(_Store):
        def put(self, tenant_org_id: str, path: str, data: bytes) -> str:
            raise OSError("object store down")

    recorder = PredictionRecorder(
        events=GovernanceEvents(GovernanceEventSettings(enabled=True), sink=Broken()), stores={"churn": BrokenStore()}
    )
    with _client(recorder=recorder) as client:
        response = client.post("/predict/churn", json={"records": RECORDS})
    assert response.status_code == 200 and len(response.json()["predictions"]) == 2
