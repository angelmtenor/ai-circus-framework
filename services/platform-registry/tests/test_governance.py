"""Tests for the admin audit trail and governance events on platform-registry's admin mutations."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

import pytest
from ai_circus_shared.governance import resolve_governance
from ai_circus_shared.governance_events import CloudEventEnvelope, GovernanceEvents, GovernanceEventSettings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from platform_registry.api import require_admin, require_authenticated, require_org_match
from platform_registry.app import app
from platform_registry.core import audit, governance, llm_settings
from platform_registry.core.db import get_session
from platform_registry.core.models import AuditLog, Base, Scenario


@dataclass
class CapturingSink:
    """An event sink that just keeps what it is sent."""

    events: list[dict[str, Any]] = field(default_factory=list)

    def send(self, event: dict[str, Any]) -> None:
        """Remember one event."""
        self.events.append(event)


@dataclass
class Harness:
    """A test client plus the handles the tests inspect."""

    client: TestClient
    sessions: sessionmaker
    sink: CapturingSink


def _build(monkeypatch: pytest.MonkeyPatch, *, audit_on: bool, events_on: bool) -> Generator[Harness]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as setup:
        setup.add(
            Scenario(
                slug="churn",
                kind="tabular_ml",
                title="Churn",
                description="d",
                icon="x",
                role_required="scenario:churn",
                industry="banking_finance",
                governance=resolve_governance("churn", None),
            )
        )
        setup.commit()

    sink = CapturingSink()
    monkeypatch.setattr(governance, "audit_enabled", lambda: audit_on)
    events = GovernanceEvents(GovernanceEventSettings(enabled=events_on, source="/platform-registry"), sink=sink)
    monkeypatch.setattr(governance, "get_events", lambda: events)

    def override_get_session() -> Generator[Session]:
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[require_admin] = lambda: None
    app.dependency_overrides[require_org_match] = lambda: None
    app.dependency_overrides[require_authenticated] = lambda: None
    try:
        yield Harness(TestClient(app), sessions, sink)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> Generator[Harness]:
    yield from _build(monkeypatch, audit_on=True, events_on=True)


@pytest.fixture
def off(monkeypatch: pytest.MonkeyPatch) -> Generator[Harness]:
    yield from _build(monkeypatch, audit_on=False, events_on=False)


def _rows(harness: Harness) -> list[AuditLog]:
    with harness.sessions() as session:
        return audit.list_audit(session, limit=500)[::-1]


def test_a_grant_is_audited_and_announced_with_the_chain_position(on: Harness) -> None:
    assert on.client.put("/entitlements/org-1/churn").status_code == 204

    (row,) = _rows(on)
    assert (row.action, row.target_id, row.org_id, row.actor) == (
        "entitlement.granted",
        "org-1/churn",
        "org-1",
        "admin-api-key",
    )
    (event,) = on.sink.events
    assert event["type"] == "dev.aicircus.governance.entitlement.changed.v1"
    assert event["tenantid"] == "org-1" and event["aisystemid"] == "churn"
    assert event["data"]["audit_id"] == row.id and event["data"]["audit_hash"] == row.hash
    CloudEventEnvelope.model_validate(event)


def test_idempotent_calls_do_not_add_noise(on: Harness) -> None:
    on.client.put("/entitlements/org-1/churn")
    on.client.put("/entitlements/org-1/churn")
    on.client.delete("/entitlements/org-1/churn")
    on.client.delete("/entitlements/org-1/churn")

    assert [r.action for r in _rows(on)] == ["entitlement.granted", "entitlement.revoked"]
    assert len(on.sink.events) == 2


def test_active_llm_and_voice_changes_are_recorded_only_when_they_change_something(on: Harness) -> None:
    model_name = next(m.model_name for spec in llm_settings.PROVIDERS.values() for m in spec.models)
    assert on.client.put("/llm-settings/active-model", json={"model_name": model_name}).status_code == 200
    on.client.put("/llm-settings/active-model", json={"model_name": model_name})
    voice = {"stt_provider": "whisper", "tts_provider": "piper"}
    assert on.client.put("/voice-settings/active", json=voice).status_code == 200
    on.client.put("/voice-settings/active", json=voice)

    rows = _rows(on)
    assert [r.action for r in rows] == ["llm_settings.active_model.set", "voice_settings.active.set"]
    assert rows[0].before == {"model_name": None} and rows[0].after == {"model_name": model_name}
    assert [e["type"].split(".governance.")[1] for e in on.sink.events] == [
        "llm.active_model.changed.v1",
        "voice.active_settings.changed.v1",
    ]


def test_the_chain_verifies_and_a_tampered_row_is_detected(on: Harness) -> None:
    for org in ("org-1", "org-2", "org-3"):
        on.client.put(f"/entitlements/{org}/churn")
    assert on.client.get("/governance/audit/verify").json() == {
        "ok": True,
        "checked": 3,
        "first_invalid_id": None,
        "reason": None,
    }

    with on.sessions() as session:
        second = _rows(on)[1]
        session.execute(update(AuditLog).where(AuditLog.id == second.id).values(org_id="someone-else"))
        session.commit()

    result = on.client.get("/governance/audit/verify").json()
    assert result["ok"] is False and result["first_invalid_id"] == second.id and result["checked"] == 1


def test_a_deleted_row_is_detected(on: Harness) -> None:
    for org in ("org-1", "org-2", "org-3"):
        on.client.put(f"/entitlements/{org}/churn")
    with on.sessions() as session:
        session.delete(session.get(AuditLog, _rows(on)[1].id))
        session.commit()

    assert on.client.get("/governance/audit/verify").json()["ok"] is False


def test_the_audit_listing_is_newest_first_pageable_and_filterable(on: Harness) -> None:
    for org in ("org-1", "org-2", "org-3"):
        on.client.put(f"/entitlements/{org}/churn")

    first = on.client.get("/governance/audit", params={"limit": 2}).json()
    assert [e["org_id"] for e in first["entries"]] == ["org-3", "org-2"]
    assert first["next_before_id"] == first["entries"][-1]["id"]
    rest = on.client.get("/governance/audit", params={"limit": 2, "before_id": first["next_before_id"]}).json()
    assert [e["org_id"] for e in rest["entries"]] == ["org-1"] and rest["next_before_id"] is None
    only = on.client.get("/governance/audit", params={"org_id": "org-2"}).json()["entries"]
    assert [e["org_id"] for e in only] == ["org-2"]
    assert on.client.get("/governance/audit", params={"limit": 501}).status_code == 422


def test_other_services_can_record_budget_changes_here(on: Harness) -> None:
    body = {
        "action": "gateway.budget.set",
        "target_type": "budget",
        "target_id": "org-1",
        "org_id": "org-1",
        "after": {"monthly_cap_usd": 25.0},
    }
    response = on.client.post("/governance/audit", json=body)
    assert response.status_code == 201 and response.json()["audit_id"] == 1

    (row,) = _rows(on)
    assert row.action == "gateway.budget.set" and row.after == {"monthly_cap_usd": 25.0}
    assert on.sink.events[0]["type"].endswith(".governance.budget.changed.v1")
    assert on.client.post("/governance/audit", json=body | {"action": "entitlement.granted"}).status_code == 422
    assert on.client.post("/governance/audit", json=body | {"extra": 1}).status_code == 422


def test_with_the_flags_off_nothing_is_recorded_or_emitted_and_the_endpoints_do_not_exist(off: Harness) -> None:
    assert off.client.put("/entitlements/org-1/churn").status_code == 204
    assert off.client.get("/entitlements/org-1/churn").json() == {"entitled": True}
    assert off.client.delete("/entitlements/org-1/churn").status_code == 204

    assert _rows(off) == [] and off.sink.events == []
    assert off.client.get("/governance/audit").status_code == 404
    assert off.client.get("/governance/audit/verify").status_code == 404
    assert off.client.post("/governance/audit", json={}).status_code == 404


def test_audit_on_but_events_off_still_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    for harness in _build(monkeypatch, audit_on=True, events_on=False):
        harness.client.put("/entitlements/org-1/churn")
        assert len(_rows(harness)) == 1 and harness.sink.events == []


def test_a_failing_event_sink_never_fails_the_admin_call(monkeypatch: pytest.MonkeyPatch) -> None:
    for harness in _build(monkeypatch, audit_on=True, events_on=True):

        class Broken:
            def send(self, event: dict) -> None:
                raise RuntimeError("valkey down")

        monkeypatch.setattr(
            governance, "get_events", lambda: GovernanceEvents(GovernanceEventSettings(enabled=True), sink=Broken())
        )
        assert harness.client.put("/entitlements/org-1/churn").status_code == 204
        assert len(_rows(harness)) == 1


def test_helpers_survive_an_unloadable_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()

    def boom() -> object:
        raise RuntimeError("no env")

    monkeypatch.setattr(governance, "get_env_config", boom)
    governance.get_events.cache_clear()
    assert governance.audit_enabled() is False
    assert governance.get_events().enabled is False
    governance.get_events.cache_clear()
