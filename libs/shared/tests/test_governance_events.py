"""
- Title:    Tests for governance events (CloudEvents envelope, sinks, outbox delivery)
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import fakeredis
import httpx
import pytest
from jsonschema import Draft202012Validator

from ai_circus_shared.governance_events import (
    EVENT_DATA_MODELS,
    OUTBOX_STREAM,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    AdminChangeData,
    CloudEventEnvelope,
    GovernanceEvents,
    GovernanceEventSettings,
    LogSink,
    ModelTrainedData,
    OutboxDeliverer,
    OutboxSink,
    assert_content_free,
    build_event,
    env_flag,
    event_type,
    finite_metrics,
    sign_body,
)


@pytest.fixture
def valkey() -> Iterator[fakeredis.FakeRedis]:
    client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    client.flushall()


def _admin_change(**overrides: Any) -> AdminChangeData:
    base = {
        "actor": "admin-api-key",
        "action": "entitlement.granted",
        "target_type": "entitlement",
        "target_id": "org-1/churn",
        "org_id": "org-1",
        "before": None,
        "after": {"entitled": True},
    }
    return AdminChangeData(**(base | overrides))


def test_event_types_are_reverse_dns_versioned_and_namespaced() -> None:
    assert event_type("budget.changed") == "dev.aicircus.governance.budget.changed.v1"
    assert event_type("model.trained", "com.example") == "com.example.governance.model.trained.v1"
    with pytest.raises(ValueError):
        event_type("nope")
    with pytest.raises(ValueError):
        event_type("budget.changed", "Not A Namespace")


def test_built_event_is_a_valid_cloudevent_with_extensions() -> None:
    event = build_event(
        "entitlement.changed",
        _admin_change(),
        source="/platform-registry",
        tenant_id="org-1",
        ai_system_id="churn",
        subject="org-1/churn",
    )
    assert event["specversion"] == "1.0"
    assert event["type"] == "dev.aicircus.governance.entitlement.changed.v1"
    assert event["tenantid"] == "org-1" and event["aisystemid"] == "churn"
    assert event["time"].endswith("Z")
    CloudEventEnvelope.model_validate(event)
    assert "before" not in event["data"], "None fields are omitted"


def test_wrong_data_model_for_an_event_name_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_event("model.trained", _admin_change(), source="/x")


@pytest.mark.parametrize(
    "bad",
    [{"prompt": "hi"}, {"nested": {"Completion": "x"}}, {"rows": [{"email": "a@b.c"}]}, {"note": "x" * 600}],
)
def test_content_looking_payloads_are_rejected(bad: dict) -> None:
    with pytest.raises(ValueError):
        assert_content_free(bad)
    with pytest.raises(ValueError):
        build_event("budget.changed", _admin_change(after=bad), source="/x")


def test_payload_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValueError):
        AdminChangeData(
            actor="a",
            action="entitlement.granted",
            target_type="t",
            target_id="i",
            prompt="leak",  # type: ignore[call-arg]
        )


def test_finite_metrics_keeps_only_finite_numbers() -> None:
    assert finite_metrics({"a": 1, "b": 0.5, "c": float("nan"), "d": "x", "e": True, "f": float("inf")}) == {
        "a": 1.0,
        "b": 0.5,
    }


def test_events_validate_against_the_published_json_schemas() -> None:
    from pathlib import Path

    schemas = Path(__file__).resolve().parents[3] / "docs" / "governance" / "schemas"
    envelope = json.loads((schemas / "cloudevent-envelope.json").read_text())
    for name, model in EVENT_DATA_MODELS.items():
        data_schema = json.loads((schemas / f"event-{name.replace('.', '-')}.v1.json").read_text())
        assert data_schema == json.loads(json.dumps(model.model_json_schema(), sort_keys=True))
        sample = (
            _admin_change()
            if model is AdminChangeData
            else ModelTrainedData(
                scenario_slug="churn", ai_system_id="churn", org_id="org-1", model_name="lgbm", metrics={"auc": 0.9}
            )
        )
        event = build_event(name, sample, source="/test")
        Draft202012Validator(envelope).validate(event)
        Draft202012Validator(data_schema).validate(event["data"])


def test_disabled_emitter_is_a_no_op(caplog: pytest.LogCaptureFixture) -> None:
    events = GovernanceEvents(GovernanceEventSettings(enabled=False))
    with caplog.at_level(logging.INFO, logger="ai_circus.governance.events"):
        assert events.emit("budget.changed", _admin_change()) is None
    assert not events.enabled and not caplog.records


def test_log_sink_writes_one_json_line(caplog: pytest.LogCaptureFixture) -> None:
    events = GovernanceEvents(GovernanceEventSettings(enabled=True, source="/svc"))
    with caplog.at_level(logging.INFO, logger="ai_circus.governance.events"):
        event = events.emit("budget.changed", _admin_change(), tenant_id="org-1")
    (record,) = caplog.records
    assert json.loads(record.getMessage()) == event
    assert isinstance(events._sink, LogSink)


def test_emit_never_raises_even_when_the_sink_fails(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        def send(self, event: dict) -> None:
            raise RuntimeError("valkey down")

    events = GovernanceEvents(GovernanceEventSettings(enabled=True), sink=Broken())
    assert events.emit("budget.changed", _admin_change()) is None
    assert "dropped" in caplog.text


def test_misconfigured_http_sink_falls_back_to_log(caplog: pytest.LogCaptureFixture) -> None:
    events = GovernanceEvents(GovernanceEventSettings(enabled=True, sink="http"))
    assert isinstance(events._sink, LogSink)
    assert "fail-open" in caplog.text


def test_from_config_reads_governance_fields_and_defaults_to_off() -> None:
    class Plain:
        pass

    assert GovernanceEventSettings.from_config(Plain(), source="/x").enabled is False

    class Secret:
        def get_secret_value(self) -> str:
            return "s3cret"

    class Cfg:
        GOVERNANCE_EVENTS_ENABLED = True
        GOVERNANCE_EVENT_SINK = "http"
        GOVERNANCE_WEBHOOK_URL = "https://hook.example/events"
        GOVERNANCE_WEBHOOK_SECRET = Secret()
        CACHE_URL = "redis://valkey:6379"

    settings = GovernanceEventSettings.from_config(Cfg(), source="/x")
    assert settings.enabled and settings.sink == "http" and settings.webhook_secret == "s3cret"


def test_outbox_sink_appends_to_the_stream(valkey: fakeredis.FakeRedis) -> None:
    events = GovernanceEvents(GovernanceEventSettings(enabled=True), sink=OutboxSink(valkey))
    event = events.emit("budget.changed", _admin_change())
    ((_, fields),) = valkey.xrange(OUTBOX_STREAM)
    assert json.loads(fields["event"]) == event


def _deliverer(valkey: fakeredis.FakeRedis, handler: Any) -> OutboxDeliverer:
    return OutboxDeliverer(
        valkey,
        url="https://hook.example/events",
        secret="s3cret",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        retry_after_ms=0,
    )


def test_delivery_is_hmac_signed_and_acknowledged(valkey: fakeredis.FakeRedis) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    OutboxSink(valkey).send(build_event("budget.changed", _admin_change(), source="/x"))
    assert _deliverer(valkey, handler).deliver_once() == 1

    (request,) = seen
    timestamp = request.headers[TIMESTAMP_HEADER]
    assert request.headers[SIGNATURE_HEADER] == sign_body("s3cret", timestamp, request.content)
    assert request.headers[SIGNATURE_HEADER] != sign_body("other", timestamp, request.content)
    assert valkey.xlen(OUTBOX_STREAM) == 0, "acknowledged events leave the outbox"


def test_events_survive_a_receiver_outage_and_arrive_after_recovery(valkey: fakeredis.FakeRedis) -> None:
    up = {"value": False}
    delivered: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not up["value"]:
            raise httpx.ConnectError("receiver down")
        delivered.append(json.loads(request.content))
        return httpx.Response(200)

    sink = OutboxSink(valkey)
    ids = []
    for index in range(3):
        event = build_event("budget.changed", _admin_change(target_id=f"org-{index}"), source="/x")
        ids.append(event["id"])
        sink.send(event)

    deliverer = _deliverer(valkey, handler)
    assert deliverer.deliver_once() == 0, "outage: nothing delivered"
    assert valkey.xlen(OUTBOX_STREAM) == 3, "...and nothing lost"

    up["value"] = True
    assert deliverer.deliver_once() == 3
    assert sorted(e["id"] for e in delivered) == sorted(ids)
    assert valkey.xlen(OUTBOX_STREAM) == 0
    assert deliverer.deliver_once() == 0, "no duplicates once acknowledged"


def test_a_non_2xx_answer_keeps_the_event_pending(valkey: fakeredis.FakeRedis) -> None:
    OutboxSink(valkey).send(build_event("budget.changed", _admin_change(), source="/x"))
    assert _deliverer(valkey, lambda request: httpx.Response(503)).deliver_once() == 0
    assert valkey.xlen(OUTBOX_STREAM) == 1


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("TRUE", True), ("1", True), ("false", False), ("", False), (None, False), (True, True)],
)
def test_env_flag_parses_env_strings(raw: object, expected: bool) -> None:
    assert env_flag(raw) is expected


def test_no_service_gives_a_governance_flag_a_profile_default() -> None:
    """Services build their config as EnvConfig(**profile defaults), and init values beat
    environment variables — so a `GOVERNANCE_*` default in settings.yaml's `environments`
    could never be switched on from the deployment's env. Unset already means off.
    """
    from pathlib import Path

    import yaml

    offenders = []
    for settings_file in sorted((Path(__file__).resolve().parents[3] / "services").glob("*/settings.yaml")):
        environments = (yaml.safe_load(settings_file.read_text()) or {}).get("environments") or {}
        for profile, values in environments.items():
            offenders += [
                f"{settings_file.parent.name}:{profile}:{key}"
                for key in (values or {})
                if str(key).startswith("GOVERNANCE_")
            ]
    assert offenders == []


def test_log_sink_makes_its_own_logger_visible_without_any_logging_setup(capsys: pytest.CaptureFixture[str]) -> None:
    """A service with no stdlib logging configured must still print the event line."""
    from ai_circus_shared import governance_events

    log = governance_events.logger
    for handler in list(log.handlers):
        log.removeHandler(handler)
    log.setLevel(logging.NOTSET)
    try:
        events = GovernanceEvents(GovernanceEventSettings(enabled=True))
        event = events.emit("budget.changed", _admin_change())
        out = capsys.readouterr().out.strip()
        assert json.loads(out) == event
    finally:
        for handler in list(log.handlers):
            log.removeHandler(handler)
