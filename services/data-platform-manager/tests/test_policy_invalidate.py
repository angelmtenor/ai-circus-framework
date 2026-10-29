"""Tests for POST /governance/policy/invalidate — HMAC auth, cache delete/overwrite, audit and events."""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

import fakeredis
import pytest
from ai_circus_shared.governance_events import GovernanceEvents, GovernanceEventSettings
from fastapi.testclient import TestClient

from data_platform_manager import api as api_module
from data_platform_manager.api import get_cache
from data_platform_manager.app import app
from data_platform_manager.core import governance_audit, policy_admin
from tests.conftest import FakeSecret

SECRET = "shared-hmac-secret"  # ruff: ignore[hardcoded-password-string]
KEY = "tenant-org-1:policy:churn"


class _Config:
    ADMIN_API_KEY = FakeSecret("admin-key")
    GOVERNANCE_POLICY_HMAC_SECRET = FakeSecret(SECRET)
    GOVERNANCE_POLICY_PUSH_TTL_SECONDS = None


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def send(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@dataclass
class Rig:
    """A client over a fake Valkey, with audit calls and events captured."""

    client: TestClient
    redis: fakeredis.FakeStrictRedis
    audits: list[dict[str, Any]] = field(default_factory=list)
    sink: _Sink = field(default_factory=_Sink)

    def post(self, payload: dict[str, Any] | bytes, *, secret: str = SECRET, timestamp: int | None = None) -> Any:
        """POST a signed request (override the secret/timestamp to forge one)."""
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        ts = str(timestamp if timestamp is not None else int(time.time()))
        headers = {"X-Governance-Timestamp": ts, "X-Governance-Signature": policy_admin.sign(secret, ts, body)}
        return self.client.post("/governance/policy/invalidate", content=body, headers=headers)


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch) -> Generator[Rig]:
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    app.dependency_overrides[get_cache] = lambda: api_module.TenantCache(_client=fake)
    monkeypatch.setattr(api_module, "get_env_config", lambda: _Config())
    harness = Rig(TestClient(app), fake)
    monkeypatch.setattr(governance_audit, "record_change", lambda **kwargs: harness.audits.append(kwargs) or True)
    events = GovernanceEvents(GovernanceEventSettings(enabled=True), sink=harness.sink)
    monkeypatch.setattr(policy_admin, "get_events", lambda: events)
    try:
        yield harness
    finally:
        app.dependency_overrides.clear()


SUSPEND = {
    "tenant_id": "org-1",
    "ai_system_id": "churn",
    "decision": {
        "decision": "deny",
        "reason_code": "suspended_by_governance",
        "policy_version": "v9",
        "obligations": {"status": "suspended"},
    },
    "ttl_seconds": 600,
}


def test_without_a_decision_it_deletes_that_tenants_cached_decisions_only(rig: Rig) -> None:
    rig.redis.hset(KEY, "gpt-4o-mini", "{}")
    rig.redis.hset("tenant-org-2:policy:churn", "gpt-4o-mini", "{}")
    rig.redis.hset("tenant-org-1:policy:titanic", "gpt-4o-mini", "{}")

    response = rig.post({"tenant_id": "org-1", "ai_system_id": "churn"})

    assert response.status_code == 200 and response.json() == {"invalidated": True, "overwritten": False}
    assert not rig.redis.exists(KEY)
    assert rig.redis.exists("tenant-org-2:policy:churn") and rig.redis.exists("tenant-org-1:policy:titanic")
    assert rig.audits[-1]["action"] == "policy.invalidated"
    assert rig.audits[-1]["after"] is None and rig.sink.events == []


def test_with_a_decision_it_overwrites_every_model_with_a_ttl_and_announces_the_status(rig: Rig) -> None:
    rig.redis.hset(KEY, "gpt-4o-mini", '{"decision": "allow"}')

    response = rig.post(SUSPEND)

    assert response.json() == {"invalidated": True, "overwritten": True}
    assert rig.redis.hkeys(KEY) == ["*"], "stale per-model entries are gone"
    stored = json.loads(rig.redis.hget(KEY, "*"))
    assert stored["decision"] == "deny" and stored["obligations"]["status"] == "suspended"
    assert 0 < rig.redis.ttl(KEY) <= 600

    audit = rig.audits[-1]
    assert audit["action"] == "policy.overwritten" and audit["target_id"] == "org-1/churn"
    assert audit["authorization"] == "Bearer admin-key"
    assert audit["after"] == {
        "decision": "deny",
        "reason_code": "suspended_by_governance",
        "status": "suspended",
        "policy_version": "v9",
    }
    (event,) = rig.sink.events
    assert event["type"] == "dev.aicircus.governance.ai_system.status_applied.v1"
    assert event["data"] == {
        "ai_system_id": "churn",
        "tenant_id": "org-1",
        "status": "suspended",
        "policy_version": "v9",
    }


def test_the_default_push_ttl_applies_when_none_is_given(rig: Rig) -> None:
    rig.post({k: v for k, v in SUSPEND.items() if k != "ttl_seconds"})
    assert 3500 < rig.redis.ttl(KEY) <= 3600


@pytest.mark.parametrize("kind", ["wrong-secret", "tampered-body", "stale", "missing"])
def test_bad_signatures_are_rejected_and_change_nothing(rig: Rig, kind: str) -> None:
    rig.redis.hset(KEY, "gpt-4o-mini", "{}")
    body = json.dumps(SUSPEND).encode()
    ts = str(int(time.time()))
    headers = {"X-Governance-Timestamp": ts, "X-Governance-Signature": policy_admin.sign(SECRET, ts, body)}
    if kind == "wrong-secret":
        headers["X-Governance-Signature"] = policy_admin.sign("other", ts, body)
    elif kind == "tampered-body":
        body = body.replace(b"deny", b"allow")
    elif kind == "stale":
        old = str(int(time.time()) - 3600)
        headers = {"X-Governance-Timestamp": old, "X-Governance-Signature": policy_admin.sign(SECRET, old, body)}
    elif kind == "missing":
        headers = {}

    response = rig.client.post("/governance/policy/invalidate", content=body, headers=headers)

    assert response.status_code == 401
    assert rig.redis.hget(KEY, "gpt-4o-mini") == "{}" and rig.audits == [] and rig.sink.events == []


def test_the_endpoint_does_not_exist_until_a_secret_is_configured(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    class NoSecret(_Config):
        GOVERNANCE_POLICY_HMAC_SECRET = None

    monkeypatch.setattr(api_module, "get_env_config", lambda: NoSecret())
    assert rig.post(SUSPEND).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"tenant_id": "org:1", "ai_system_id": "churn"},
        {"tenant_id": "org-1", "ai_system_id": "Churn System"},
        {"tenant_id": "org-1", "ai_system_id": "churn", "extra": 1},
        {"tenant_id": "org-1", "ai_system_id": "churn", "ttl_seconds": 0},
        {"tenant_id": "org-1", "ai_system_id": "churn", "decision": {"decision": "maybe", "reason_code": "x"}},
        {
            "tenant_id": "org-1",
            "ai_system_id": "churn",
            "decision": {"decision": "allow", "reason_code": "x", "obligations": {"roles": []}},
        },
    ],
)
def test_malformed_bodies_are_422_without_echoing_input(rig: Rig, payload: dict) -> None:
    response = rig.post(payload)
    assert response.status_code == 422
    assert "org:1" not in response.text


def test_oversized_bodies_are_refused(rig: Rig) -> None:
    assert rig.post(b"x" * (policy_admin.MAX_BODY_BYTES + 1)).status_code == 413


def test_signature_check_primitives() -> None:
    body = b"{}"
    now = 1_800_000_000
    good = policy_admin.sign(SECRET, str(now), body)
    assert policy_admin.signature_is_valid(SECRET, timestamp=str(now), signature=good, body=body, now=now + 10)
    assert not policy_admin.signature_is_valid(SECRET, timestamp=str(now), signature=good, body=body, now=now + 301)
    assert not policy_admin.signature_is_valid(SECRET, timestamp="abc", signature=good, body=body, now=now)
    assert not policy_admin.signature_is_valid(SECRET, timestamp=str(now), signature=None, body=body, now=now)
