"""
- Title:    Tests for non-LLM decision records (prediction.recorded) and their retention
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from typing import Any

import pytest

from ai_circus_shared.decision_records import (
    DEFAULT_RETENTION_DAYS,
    RECORD_PREFIX,
    PredictionRecorder,
    build_recorder,
    model_version,
    summarise,
    trace_id_from,
)
from ai_circus_shared.governance_events import GovernanceEvents, GovernanceEventSettings, PredictionRecordData

TRACE = "0af7651916cd43dd8448eb211c80319c"


class MemoryStore:
    """An in-memory stand-in for the tenant-prefixed object store."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put(self, tenant_org_id: str, path: str, data: bytes) -> str:
        self.objects[(tenant_org_id, path)] = data
        return path

    def list(self, tenant_org_id: str, prefix: str = "") -> list[str]:
        return sorted(p for (org, p) in self.objects if org == tenant_org_id and p.startswith(prefix))

    def delete(self, tenant_org_id: str, path: str) -> None:
        self.objects.pop((tenant_org_id, path), None)


class Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def send(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _data(org: str = "org-1", **overrides: Any) -> PredictionRecordData:
    base: dict[str, Any] = {
        "scenario_slug": "churn",
        "ai_system_id": "churn",
        "org_id": org,
        "model_name": "lgbm",
        "model_version": "abc123def456",
        "prediction_count": 2,
        "outcomes": {"scored": 2},
        "score_min": 0.2,
        "score_max": 0.8,
        "explanation_available": True,
        "trace_id": TRACE,
    }
    return PredictionRecordData(**(base | overrides))


def _recorder(
    *, events: bool = True, store: MemoryStore | None = None, days: int = 183
) -> tuple[PredictionRecorder, Sink]:
    sink = Sink()
    emitter = GovernanceEvents(GovernanceEventSettings(enabled=events, source="/prediction"), sink=sink)
    return PredictionRecorder(
        events=emitter, stores={"churn": store} if store is not None else {}, retention_days=days
    ), sink


def test_model_version_changes_exactly_when_the_artifacts_do() -> None:
    a = model_version({"checksums": {"model.joblib": "aa", "metadata.json": "bb"}})
    assert a == model_version({"checksums": {"metadata.json": "bb", "model.joblib": "aa"}})
    assert a != model_version({"checksums": {"model.joblib": "aa", "metadata.json": "cc"}})
    assert len(a) == 12 and set(a) <= set("0123456789abcdef")


def test_model_version_falls_back_to_identifying_metadata_then_unversioned() -> None:
    fallback = model_version({"model_name": "lgbm", "test_score": 0.9, "training_rows": 100})
    assert fallback != "unversioned" and fallback == model_version(
        {"model_name": "lgbm", "test_score": 0.9, "training_rows": 100}
    )
    assert model_version({}) == "unversioned"


def test_trace_id_comes_from_a_w3c_traceparent_or_is_generated() -> None:
    assert trace_id_from({"traceparent": f"00-{TRACE}-b7ad6b7169203331-01"}) == TRACE
    for headers in ({}, {"traceparent": "garbage"}, {"traceparent": f"00-{'0' * 31}-b7ad6b7169203331-01"}):
        generated = trace_id_from(headers)
        assert len(generated) == 32 and generated != TRACE
    assert trace_id_from({}) != trace_id_from({})


def test_summarise_builds_a_histogram_and_the_score_range() -> None:
    assert summarise(["a", "b", "a"], [0.5, 0.25, 0.75]) == ({"a": 2, "b": 1}, 0.25, 0.75)
    assert summarise([], []) == ({}, None, None)


def test_a_record_is_emitted_as_a_content_free_event() -> None:
    recorder, sink = _recorder()
    recorder.record(_data())
    (event,) = sink.events
    assert event["type"] == "dev.aicircus.governance.prediction.recorded.v1"
    assert event["tenantid"] == "org-1" and event["aisystemid"] == "churn" and event["subject"] == "churn"
    assert event["data"]["trace_id"] == TRACE and event["data"]["explanation_available"] is True


def test_the_record_store_writes_one_tenant_scoped_object_per_request() -> None:
    store = MemoryStore()
    recorder, _ = _recorder(events=False, store=store)
    recorder.record(_data(), now=datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    recorder.record(_data(org="org-2"), now=datetime(2026, 10, 29, 10, 0, tzinfo=UTC))

    (org1_key,) = store.list("org-1")
    assert org1_key.startswith(f"{RECORD_PREFIX}/2026-10-29/{TRACE}-") and org1_key.endswith(".json")
    assert len(store.list("org-2")) == 1, "each tenant's records are under its own prefix"
    stored = json.loads(store.objects[("org-1", org1_key)])
    assert stored["org_id"] == "org-1" and stored["recorded_at"] == "2026-10-29T10:00:00.000Z"
    assert "features" not in stored and "contributions" not in stored


def test_retention_window_boundaries() -> None:
    store = MemoryStore()
    recorder, _ = _recorder(events=False, store=store, days=30)
    today = date(2026, 10, 29)  # cutoff = 2026-09-29: the 29th is kept, the 28th goes
    for day in ("2026-09-28", "2026-09-29", "2026-10-29"):
        store.put("org-1", f"{RECORD_PREFIX}/{day}/r.json", b"{}")
    store.put("org-1", "datasets/keep.csv", b"x")
    store.put("org-2", f"{RECORD_PREFIX}/2020-01-01/r.json", b"{}")

    assert recorder.prune("org-1", "churn", today=today) == 1
    assert store.list("org-1") == [
        "datasets/keep.csv",
        f"{RECORD_PREFIX}/2026-09-29/r.json",
        f"{RECORD_PREFIX}/2026-10-29/r.json",
    ]
    assert len(store.list("org-2")) == 1, "another tenant's records are never touched"


def test_pruning_runs_at_most_once_a_day_per_tenant() -> None:
    store = MemoryStore()
    recorder, _ = _recorder(events=False, store=store, days=30)
    store.put("org-1", f"{RECORD_PREFIX}/2020-01-01/r.json", b"{}")
    assert recorder.prune("org-1", "churn", today=date(2026, 10, 29)) == 1
    store.put("org-1", f"{RECORD_PREFIX}/2020-01-02/r.json", b"{}")
    assert recorder.prune("org-1", "churn", today=date(2026, 10, 29)) == 0, "already pruned today"
    assert recorder.prune("org-1", "churn", today=date(2026, 10, 30)) == 1


def test_writing_a_record_triggers_the_daily_prune() -> None:
    store = MemoryStore()
    recorder, _ = _recorder(events=False, store=store, days=30)
    store.put("org-1", f"{RECORD_PREFIX}/2020-01-01/r.json", b"{}")
    recorder.record(_data(), now=datetime(2026, 10, 29, tzinfo=UTC))
    assert not any("2020-01-01" in key for key in store.list("org-1"))


def test_disabled_recorder_does_nothing() -> None:
    recorder, sink = _recorder(events=False, store=None)
    assert not recorder.enabled
    recorder.record(_data())
    assert sink.events == []


def test_a_failing_store_or_sink_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    class Broken(MemoryStore):
        def put(self, tenant_org_id: str, path: str, data: bytes) -> str:
            raise OSError("object store down")

    recorder, sink = _recorder(store=Broken())
    with caplog.at_level(logging.ERROR, logger="ai_circus.governance.records"):
        recorder.record(_data())
    assert "not stored" in caplog.text
    assert len(sink.events) == 1, "the event still went out"


def test_build_recorder_reads_the_flags_and_defaults_to_off() -> None:
    class Off:
        pass

    off = build_recorder(Off(), source="/prediction", stores={"churn": MemoryStore()})
    assert not off.enabled and off.retention_days == DEFAULT_RETENTION_DAYS

    class On:
        GOVERNANCE_EVENTS_ENABLED = "true"
        GOVERNANCE_RECORD_STORE = "ObjectStore"
        GOVERNANCE_RETENTION_DAYS = "400"

    on = build_recorder(On(), source="/prediction", stores={"churn": MemoryStore()})
    assert on.events.enabled and set(on.stores) == {"churn"} and on.retention_days == 400

    class NoStoreGiven(On):
        pass

    assert build_recorder(NoStoreGiven(), source="/p", stores=None).stores == {}


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "", None])
def test_a_bad_retention_value_falls_back_to_the_default(raw: str | None) -> None:
    class Cfg:
        GOVERNANCE_RETENTION_DAYS = raw

    assert build_recorder(Cfg(), source="/p").retention_days == DEFAULT_RETENTION_DAYS


def test_retention_default_is_at_least_six_months() -> None:
    assert DEFAULT_RETENTION_DAYS >= 183


def test_each_scenario_records_into_its_own_bucket() -> None:
    churn, titanic = MemoryStore(), MemoryStore()
    sink = Sink()
    recorder = PredictionRecorder(
        events=GovernanceEvents(GovernanceEventSettings(enabled=False), sink=sink),
        stores={"churn": churn, "titanic": titanic},
    )
    recorder.record(_data(scenario_slug="titanic"))
    recorder.record(_data(scenario_slug="unknown_scenario"))
    assert len(titanic.list("org-1")) == 1 and churn.list("org-1") == []
