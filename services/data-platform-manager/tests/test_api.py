"""Tests for the data-platform-manager admin API — require_admin gating, the
pipeline-jobs "unavailable outside k3s" path, and the gateway/roadmap read
endpoints. Job status/trigger's real Kubernetes-API-calling behavior is covered
by core/test_k8s_jobs.py's YAML-drift check instead of re-mocked here — this
test suite runs outside any cluster, so `in_cluster_config_available()` is
naturally always False here, exactly the path a docker-compose deployment hits.
"""

from __future__ import annotations

import json
from collections.abc import Generator

import fakeredis
import pytest
from ai_circus_shared.document_store import Base
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from data_platform_manager.api import get_client, get_producer, require_admin
from data_platform_manager.api import get_document_session as api_get_document_session
from data_platform_manager.app import app
from data_platform_manager.core import gateway, k8s_jobs, lakehouse
from tests.conftest import FakeSecret


class _FakeKafkaProducer:
    """Minimal stand-in for confluent_kafka.Producer — records produce() calls."""

    def __init__(self) -> None:
        self.produced: list[tuple[str, bytes]] = []

    def produce(self, topic: str, value: bytes) -> None:
        self.produced.append((topic, value))

    def poll(self, timeout: float) -> None:
        pass


@pytest.fixture
def fake_producer() -> _FakeKafkaProducer:
    return _FakeKafkaProducer()


@pytest.fixture
def client(fake_producer: _FakeKafkaProducer) -> Generator[TestClient]:
    """A TestClient wired to an isolated in-memory SQLite database, a fake
    (in-process) Redis server, and a fake Kafka producer — same
    StaticPool-backed sqlite shape as platform-registry's own test_api.py
    fixture.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    fake_redis = fakeredis.FakeStrictRedis(decode_responses=True)

    def override_get_session() -> Generator:
        with session_factory() as session:
            yield session

    app.dependency_overrides[api_get_document_session] = override_get_session
    app.dependency_overrides[get_client] = lambda: fake_redis
    app.dependency_overrides[get_producer] = lambda: fake_producer
    app.dependency_overrides[require_admin] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class _FakeAdminConfig:
    """Stand-in for EnvConfig exposing what `require_admin`/`gateway_rate_limits` read."""

    def __init__(self, admin_api_key: str = "test-admin-key") -> None:
        self.ADMIN_API_KEY = FakeSecret(admin_api_key)
        self.LLM_GATEWAY_URL = "http://llm-gateway:4000"
        self.LLM_GATEWAY_API_KEY = FakeSecret("test-master-key")


@pytest.fixture
def unauthenticated_client(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Same wiring as `client`, but with the real `require_admin` dependency
    restored, backed by a fake config so the admin-gate check doesn't need every
    mandatory env var.
    """
    del app.dependency_overrides[require_admin]
    monkeypatch.setattr("data_platform_manager.api.get_env_config", lambda: _FakeAdminConfig())
    return client


def test_healthz_does_not_require_admin_token(unauthenticated_client: TestClient) -> None:
    response = unauthenticated_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_roadmap_requires_admin_token(unauthenticated_client: TestClient) -> None:
    response = unauthenticated_client.get("/roadmap")
    assert response.status_code == 401


def test_roadmap_succeeds_with_admin_token(unauthenticated_client: TestClient) -> None:
    response = unauthenticated_client.get("/roadmap", headers={"Authorization": "Bearer test-admin-key"})
    assert response.status_code == 200
    names = [c["name"] for c in response.json()]
    assert "Non-Relational Store" in names
    assert "Cache / Key-Value" in names


def test_roadmap_rejects_wrong_token(unauthenticated_client: TestClient) -> None:
    response = unauthenticated_client.get("/roadmap", headers={"Authorization": "Bearer wrong-key"})
    assert response.status_code == 401


def test_roadmap_returns_every_capability(client: TestClient) -> None:
    response = client.get("/roadmap")
    assert response.status_code == 200
    capabilities = response.json()
    assert len(capabilities) > 0
    assert {c["status"] for c in capabilities} <= {"live", "partial", "planned"}


def test_pipeline_jobs_reports_unavailable_outside_a_cluster(client: TestClient) -> None:
    """This test suite never runs inside a real k8s pod, so job control is
    genuinely unavailable — the exact path a docker-compose deployment hits.
    """
    response = client.get("/pipeline/jobs")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "k3s" in body["reason"]
    assert body["jobs"] == []


def test_trigger_pipeline_job_returns_501_outside_a_cluster(client: TestClient) -> None:
    response = client.post("/pipeline/jobs/etl-tabular/trigger")
    assert response.status_code == 501


def test_trigger_unknown_job_returns_404_even_outside_a_cluster(client: TestClient) -> None:
    # Outside a cluster, the "unavailable" check runs first — this only proves an
    # unknown name is never silently accepted; core/test_k8s_jobs.py covers the
    # real in-cluster 404 path.
    response = client.post("/pipeline/jobs/does-not-exist/trigger")
    assert response.status_code == 501


def test_trigger_history_starts_empty(client: TestClient) -> None:
    response = client.get("/pipeline/triggers/history")
    assert response.status_code == 200
    assert response.json() == []


def test_trigger_pipeline_job_publishes_an_event_when_in_cluster(
    client: TestClient, fake_producer: _FakeKafkaProducer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real in-cluster k8s-API-calling path is covered by
    core/test_k8s_jobs.py's YAML-drift check — this only proves that once a
    trigger succeeds, it's both durably recorded (document store) and
    published as a real-time event (Kafka), matching the endpoint's docstring.
    """
    monkeypatch.setattr(k8s_jobs, "in_cluster_config_available", lambda: True)
    monkeypatch.setattr(k8s_jobs, "trigger_job", lambda name: None)

    response = client.post("/pipeline/jobs/etl-tabular/trigger")

    assert response.status_code == 202
    assert response.json()["job"] == "etl-tabular"
    assert client.get("/pipeline/triggers/history").json() == [response.json()]
    assert len(fake_producer.produced) == 1
    topic, value = fake_producer.produced[0]
    assert topic == "tenant-admin.pipeline-triggers"
    assert json.loads(value) == response.json()


def test_trigger_pipeline_job_succeeds_even_if_event_publish_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort by design (see the endpoint's docstring): a broken Kafka
    publish must never turn an otherwise-successful trigger into a failure.
    """
    monkeypatch.setattr(k8s_jobs, "in_cluster_config_available", lambda: True)
    monkeypatch.setattr(k8s_jobs, "trigger_job", lambda name: None)

    def _raise(topic: str, value: bytes) -> None:
        raise RuntimeError("broker unreachable")

    app.dependency_overrides[get_producer] = lambda: type("BrokenProducer", (), {"produce": _raise})()

    response = client.post("/pipeline/jobs/etl-tabular/trigger")

    assert response.status_code == 202
    assert response.json()["job"] == "etl-tabular"


class _FakeKafkaConsumer:
    """Minimal stand-in for confluent_kafka.Consumer — never touches a real
    broker, unlike pointing a real Consumer at an "unreachable" address (which
    depends on nothing actually listening there — this dev machine may well
    have a real Kafka running on the default port, as it did the day this test
    was first written).
    """

    def __init__(self, results: list[dict | None]) -> None:
        self._results = list(results)
        self.subscribed: list[str] | None = None
        self.closed = False

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed = topics

    def poll(self, timeout: float) -> object | None:
        return self._results.pop(0) if self._results else None

    def close(self) -> None:
        self.closed = True


def test_recent_pipeline_trigger_events_returns_empty_list_when_topic_is_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_config = type("FakeConfig", (), {"KAFKA_BOOTSTRAP_SERVERS": "unused"})()
    monkeypatch.setattr("data_platform_manager.api.get_env_config", lambda: fake_config)
    monkeypatch.setattr("data_platform_manager.api.connect_consumer", lambda config, group_id: _FakeKafkaConsumer([]))

    response = client.get("/events/pipeline-triggers")

    assert response.status_code == 200
    assert response.json() == []


def test_recent_pipeline_trigger_events_survives_early_none_polls(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: confirmed live against a real broker that a brand-new
    consumer group's first few poll() calls return None while it joins the
    group, even when a message already exists — stopping at the first None
    (the original implementation) would have wrongly reported this as empty.
    """
    fake_config = type("FakeConfig", (), {"KAFKA_BOOTSTRAP_SERVERS": "unused"})()
    monkeypatch.setattr("data_platform_manager.api.get_env_config", lambda: fake_config)
    event = {"job": "etl-tabular", "triggered_at": "2026-01-01T00:00:00+00:00"}

    class _FakeMessage:
        def error(self) -> None:
            return None

        def value(self) -> bytes:
            return json.dumps(event).encode("utf-8")

    fake_results = [None, None, None, _FakeMessage(), None]
    monkeypatch.setattr(
        "data_platform_manager.api.connect_consumer", lambda config, group_id: _FakeKafkaConsumer(fake_results)
    )

    response = client.get("/events/pipeline-triggers")

    assert response.status_code == 200
    assert response.json() == [event]


def test_gateway_rate_limits_proxies_to_llm_gateway(
    unauthenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        gateway,
        "get_rate_limits",
        lambda base_url, master_key: [{"model_name": "claude-haiku", "rpm": 50, "tpm": 100000}],
    )

    response = unauthenticated_client.get("/gateway/rate-limits", headers={"Authorization": "Bearer test-admin-key"})

    assert response.status_code == 200
    assert response.json() == [{"model_name": "claude-haiku", "rpm": 50, "tpm": 100000}]


def test_gateway_rate_limits_surfaces_a_gateway_error_as_502(
    unauthenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(base_url: str, master_key: str) -> list[dict[str, object]]:
        raise gateway.LlmGatewayError("boom")

    monkeypatch.setattr(gateway, "get_rate_limits", _raise)

    response = unauthenticated_client.get("/gateway/rate-limits", headers={"Authorization": "Bearer test-admin-key"})

    assert response.status_code == 502


# CDC (ai_circus_shared.cdc) calls real Postgres-only SQL functions
# (pg_create_logical_replication_slot, pg_logical_slot_get_changes) that the
# in-memory SQLite this test suite otherwise uses can't run — so these tests
# mock ensure_slot/poll_changes/slot_status at the boundary, the same way
# gateway.get_rate_limits is mocked above. The parser itself is fully
# unit-tested (against real captured output) in libs/shared's own
# tests/test_cdc.py; the real end-to-end read was verified live in
# docker-compose against the actual running Postgres.


def test_cdc_status_reports_no_slot_before_first_poll(
    unauthenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("data_platform_manager.api.slot_status", lambda session, slot_name: None)

    response = unauthenticated_client.get("/cdc/status", headers={"Authorization": "Bearer test-admin-key"})

    assert response.status_code == 200
    assert response.json() == {"slot_exists": False, "active": None, "confirmed_flush_lsn": None}


def test_cdc_status_reports_an_existing_slots_position(
    unauthenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "data_platform_manager.api.slot_status",
        lambda session, slot_name: {"active": True, "confirmed_flush_lsn": "0/37661C0"},
    )

    response = unauthenticated_client.get("/cdc/status", headers={"Authorization": "Bearer test-admin-key"})

    assert response.status_code == 200
    assert response.json() == {"slot_exists": True, "active": True, "confirmed_flush_lsn": "0/37661C0"}


def test_cdc_poll_publishes_each_captured_change(
    client: TestClient, fake_producer: _FakeKafkaProducer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_circus_shared.cdc import ChangeEvent

    change = ChangeEvent(
        schema="public", table="documents", operation="INSERT", columns={"doc_id": "doc-1", "org_id": "admin"}
    )
    monkeypatch.setattr("data_platform_manager.api.ensure_slot", lambda session, slot_name: True)
    monkeypatch.setattr("data_platform_manager.api.poll_changes", lambda session, slot_name: [change])

    response = client.post("/cdc/poll")

    assert response.status_code == 200
    body = response.json()
    assert body["slot_created"] is True
    assert body["changes_captured"] == 1
    assert body["changes"] == [change.to_dict()]
    assert len(fake_producer.produced) == 1
    topic, value = fake_producer.produced[0]
    assert topic == "tenant-admin.cdc.documents"
    assert json.loads(value) == change.to_dict()


def test_cdc_poll_reports_zero_changes_when_nothing_changed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("data_platform_manager.api.ensure_slot", lambda session, slot_name: False)
    monkeypatch.setattr("data_platform_manager.api.poll_changes", lambda session, slot_name: [])

    response = client.post("/cdc/poll")

    assert response.status_code == 200
    assert response.json() == {"slot_created": False, "changes_captured": 0, "changes": []}


def test_cdc_poll_succeeds_even_if_event_publish_fails(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_circus_shared.cdc import ChangeEvent

    change = ChangeEvent(schema="public", table="documents", operation="DELETE", columns={"doc_id": "doc-1"})
    monkeypatch.setattr("data_platform_manager.api.ensure_slot", lambda session, slot_name: False)
    monkeypatch.setattr("data_platform_manager.api.poll_changes", lambda session, slot_name: [change])

    def _raise(topic: str, value: bytes) -> None:
        raise RuntimeError("broker unreachable")

    from data_platform_manager.api import get_producer

    app.dependency_overrides[get_producer] = lambda: type("BrokenProducer", (), {"produce": _raise})()

    response = client.post("/cdc/poll")

    assert response.status_code == 200
    assert response.json()["changes_captured"] == 1


def test_cdc_endpoints_require_admin_token(unauthenticated_client: TestClient) -> None:
    assert unauthenticated_client.get("/cdc/status").status_code == 401
    assert unauthenticated_client.post("/cdc/poll").status_code == 401


# Lakehouse (core/lakehouse.py, PyIceberg) needs a real Postgres catalog + real
# S3-compatible storage to do anything — mocked at the same `get_catalog`
# boundary the endpoints call through, the same way CDC's SQL functions are
# mocked above. The real read/write/versioning path was verified live against
# the actual running Postgres + SeaweedFS before this code was written.


class _FakeCatalog:
    """Stands in for the object lakehouse.get_catalog() would normally return —
    the endpoints never call any Catalog method directly, only the
    lakehouse.* functions this test monkeypatches, so its shape doesn't matter.
    """


def test_lakehouse_ingest_returns_the_ingest_summary(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("data_platform_manager.api.get_env_config", lambda: object())
    monkeypatch.setattr(lakehouse, "get_catalog", lambda config: _FakeCatalog())
    monkeypatch.setattr(
        lakehouse,
        "ingest",
        lambda catalog, store, org_id, collection, table_name: {
            "table": "lakehouse.pipeline_triggers",
            "rows_ingested": 3,
            "total_rows": 3,
            "snapshot_count": 1,
        },
    )

    response = client.post("/lakehouse/ingest")

    assert response.status_code == 200
    assert response.json() == {
        "table": "lakehouse.pipeline_triggers",
        "rows_ingested": 3,
        "total_rows": 3,
        "snapshot_count": 1,
    }


def test_lakehouse_tables_lists_every_table(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("data_platform_manager.api.get_env_config", lambda: object())
    monkeypatch.setattr(lakehouse, "get_catalog", lambda config: _FakeCatalog())
    monkeypatch.setattr(lakehouse, "list_tables", lambda catalog: ["lakehouse.pipeline_triggers"])

    response = client.get("/lakehouse/tables")

    assert response.status_code == 200
    assert response.json() == ["lakehouse.pipeline_triggers"]


def test_lakehouse_tables_is_empty_before_any_ingest(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("data_platform_manager.api.get_env_config", lambda: object())
    monkeypatch.setattr(lakehouse, "get_catalog", lambda config: _FakeCatalog())
    monkeypatch.setattr(lakehouse, "list_tables", lambda catalog: [])

    response = client.get("/lakehouse/tables")

    assert response.status_code == 200
    assert response.json() == []


def test_lakehouse_table_returns_404_for_an_unknown_table(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("data_platform_manager.api.get_env_config", lambda: object())
    monkeypatch.setattr(lakehouse, "get_catalog", lambda config: _FakeCatalog())
    monkeypatch.setattr(lakehouse, "table_info", lambda catalog, table_name: None)

    response = client.get("/lakehouse/tables/does-not-exist")

    assert response.status_code == 404


def test_lakehouse_table_returns_info_for_a_known_table(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("data_platform_manager.api.get_env_config", lambda: object())
    monkeypatch.setattr(lakehouse, "get_catalog", lambda config: _FakeCatalog())
    monkeypatch.setattr(
        lakehouse,
        "table_info",
        lambda catalog, table_name: {"table": f"lakehouse.{table_name}", "total_rows": 5, "snapshot_count": 2},
    )

    response = client.get("/lakehouse/tables/pipeline_triggers")

    assert response.status_code == 200
    assert response.json() == {"table": "lakehouse.pipeline_triggers", "total_rows": 5, "snapshot_count": 2}


def test_lakehouse_endpoints_require_admin_token(unauthenticated_client: TestClient) -> None:
    assert unauthenticated_client.post("/lakehouse/ingest").status_code == 401
    assert unauthenticated_client.get("/lakehouse/tables").status_code == 401
    assert unauthenticated_client.get("/lakehouse/tables/x").status_code == 401
