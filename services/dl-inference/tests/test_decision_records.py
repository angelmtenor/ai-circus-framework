"""Tests for the decision record each /predict request leaves (docs/governance/)."""

from __future__ import annotations

import json
from collections.abc import Generator
from types import SimpleNamespace
from typing import Any

import pytest
from ai_circus_shared.auth import Identity
from ai_circus_shared.decision_records import PredictionRecorder
from ai_circus_shared.governance_events import GovernanceEvents, GovernanceEventSettings
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dl_inference import api
from dl_inference.core.identity import resolve_identity
from dl_inference.core.model_cache import DlModelCache
from tests.fixtures import MemoryStore, publish

TRACE = "0af7651916cd43dd8448eb211c80319c"


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def send(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _client(recorder: PredictionRecorder | None, *, slug_governance: object = None) -> TestClient:
    store = MemoryStore()
    publish(store, "demo", "text")
    app = FastAPI()
    app.include_router(api.router)
    app.state.definitions = {"scn": SimpleNamespace(slug="scn", governance=slug_governance)}
    app.state.model_cache = DlModelCache({"scn": store}, fallback_org_id="demo", threads=1)  # type: ignore[dict-item]
    if recorder is not None:
        app.state.recorder = recorder
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="acme", roles=frozenset())
    return TestClient(app)


@pytest.fixture
def rig() -> Generator[tuple[TestClient, _Sink]]:
    sink = _Sink()
    recorder = PredictionRecorder(events=GovernanceEvents(GovernanceEventSettings(enabled=True), sink=sink))
    yield _client(recorder), sink


def test_a_prediction_leaves_one_content_free_record(rig: tuple[TestClient, _Sink]) -> None:
    client, sink = rig
    private_text = "i have fever and a very private rash"
    response = client.post(
        "/predict/scn",
        json={"text": private_text},
        headers={"traceparent": f"00-{TRACE}-b7ad6b7169203331-01"},
    )
    assert response.status_code == 200
    predicted = response.json()["predicted"]

    (event,) = sink.events
    assert event["type"] == "dev.aicircus.governance.prediction.recorded.v1"
    assert event["tenantid"] == "acme" and event["aisystemid"] == "scn"
    data = event["data"]
    assert data["scenario_slug"] == "scn" and data["org_id"] == "acme"
    assert data["prediction_count"] == 1 and data["outcomes"] == {predicted: 1}
    assert data["explanation_available"] is True, "explain defaults on and the text model returns token weights"
    assert data["trace_id"] == TRACE
    assert 0 < data["score_max"] <= 1 and len(data["model_version"]) in {12} | {len("unversioned")}
    assert private_text not in json.dumps(event) and "private" not in json.dumps(event)


def test_without_an_explanation_the_record_says_so(rig: tuple[TestClient, _Sink]) -> None:
    client, sink = rig
    client.post("/predict/scn", json={"text": "fever and rash", "explain": False})
    assert sink.events[0]["data"]["explanation_available"] is False


def test_a_generated_trace_id_is_used_when_the_caller_sent_none(rig: tuple[TestClient, _Sink]) -> None:
    client, sink = rig
    client.post("/predict/scn", json={"text": "fever and rash"})
    client.post("/predict/scn", json={"text": "fever and rash"})
    first, second = (e["data"]["trace_id"] for e in sink.events)
    assert len(first) == 32 and first != second


def test_a_declared_ai_system_id_is_used() -> None:
    from ai_circus_shared.governance import GovernanceConfig

    sink = _Sink()
    recorder = PredictionRecorder(events=GovernanceEvents(GovernanceEventSettings(enabled=True), sink=sink))
    client = _client(recorder, slug_governance=GovernanceConfig(ai_system_id="scn-v2"))
    client.post("/predict/scn", json={"text": "fever and rash"})
    assert sink.events[0]["data"]["ai_system_id"] == "scn-v2"


def test_with_no_recorder_or_everything_off_prediction_behaves_as_before() -> None:
    off = PredictionRecorder(events=GovernanceEvents(GovernanceEventSettings(enabled=False)))
    for recorder in (None, off):
        response = _client(recorder).post("/predict/scn", json={"text": "i have fever and rash"})
        assert response.status_code == 200 and response.json()["predicted"] == "dengue"


def test_a_broken_sink_never_fails_the_prediction() -> None:
    class Broken:
        def send(self, event: dict) -> None:
            raise RuntimeError("down")

    recorder = PredictionRecorder(events=GovernanceEvents(GovernanceEventSettings(enabled=True), sink=Broken()))
    response = _client(recorder).post("/predict/scn", json={"text": "i have fever and rash"})
    assert response.status_code == 200
