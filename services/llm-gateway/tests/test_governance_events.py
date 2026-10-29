"""Tests for llm-gateway's self-contained governance event emitter."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import fakeredis
import pytest
from jsonschema import Draft202012Validator

from llm_gateway.governance_events import OUTBOX_STREAM, GatewayEvents, build_event, env_flag

SCHEMAS = Path(__file__).resolve().parents[3] / "docs" / "governance" / "schemas"
DECISION = {
    "tenant_id": "org-1",
    "ai_system_id": "churn",
    "model": "m",
    "call_type": "completion",
    "decision": "deny",
    "reason_code": "x",
    "fail_open": True,
}
BUDGET = {"org_id": "org-1", "period": "2026-10", "spent_usd": 10.0, "cap_usd": 10.0}


def test_events_validate_against_the_published_schemas() -> None:
    envelope = json.loads((SCHEMAS / "cloudevent-envelope.json").read_text())
    for name, data in (("policy.decision", DECISION), ("budget.exhausted", BUDGET)):
        event = build_event(name, data, tenant_id="org-1", ai_system_id="churn")
        Draft202012Validator(envelope).validate(event)
        Draft202012Validator(json.loads((SCHEMAS / f"event-{name.replace('.', '-')}.v1.json").read_text())).validate(
            event["data"]
        )


@pytest.mark.parametrize(
    "name,data",
    [
        ("policy.decision", {**DECISION, "prompt": "hi"}),
        ("policy.decision", {**DECISION, "extra": 1}),
        ("policy.decision", {**DECISION, "reason_code": "x" * 600}),
        ("budget.exhausted", {**BUDGET, "token": "t"}),
        ("nope", {}),
    ],
)
def test_content_or_unknown_fields_are_rejected(name: str, data: dict) -> None:
    with pytest.raises(ValueError):
        build_event(name, data)


def test_off_by_default_and_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    assert GatewayEvents().emit("policy.decision", DECISION) is None
    broken = GatewayEvents(enabled=True, sink="http", client=object())
    assert broken.emit("policy.decision", DECISION) is None
    assert "dropped" in caplog.text


def test_log_sink_prints_a_json_line(caplog: pytest.LogCaptureFixture) -> None:
    events = GatewayEvents(enabled=True)
    with caplog.at_level(logging.INFO, logger="ai_circus.governance.events"):
        event = events.emit("budget.exhausted", BUDGET, tenant_id="org-1")
    assert json.loads(caplog.records[-1].getMessage()) == event


def test_http_sink_appends_to_the_shared_outbox_stream() -> None:
    client = fakeredis.FakeRedis()
    event = GatewayEvents(enabled=True, sink="http", client=client).emit("budget.exhausted", BUDGET)
    ((_, fields),) = client.xrange(OUTBOX_STREAM)
    assert json.loads(fields[b"event"]) == event


def test_from_config_reads_flags() -> None:
    class Cfg:
        GOVERNANCE_EVENTS_ENABLED = "true"
        GOVERNANCE_EVENT_SINK = "http"
        GOVERNANCE_EVENT_NAMESPACE = "com.example"

    events = GatewayEvents.from_config(Cfg(), client_factory=fakeredis.FakeRedis)
    assert events.enabled and events.emit("budget.exhausted", BUDGET)["type"].startswith("com.example.governance.")  # type: ignore[index]
    assert not GatewayEvents.from_config(object()).enabled


@pytest.mark.parametrize("raw,expected", [("TRUE", True), ("1", True), ("false", False), (None, False)])
def test_env_flag(raw: object, expected: bool) -> None:
    assert env_flag(raw) is expected
