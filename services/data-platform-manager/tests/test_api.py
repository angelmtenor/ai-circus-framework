"""Tests for the data-platform-manager admin API — require_admin gating, the
pipeline-jobs "unavailable outside k3s" path, and the gateway/roadmap read
endpoints. Job status/trigger's real Kubernetes-API-calling behavior is covered
by core/test_k8s_jobs.py's YAML-drift check instead of re-mocked here — this
test suite runs outside any cluster, so `in_cluster_config_available()` is
naturally always False here, exactly the path a docker-compose deployment hits.
"""

from __future__ import annotations

from collections.abc import Generator

import fakeredis
import pytest
from ai_circus_shared.document_store import Base
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from data_platform_manager.api import get_client, require_admin
from data_platform_manager.api import get_document_session as api_get_document_session
from data_platform_manager.app import app
from data_platform_manager.core import gateway
from tests.conftest import FakeSecret


@pytest.fixture
def client() -> Generator[TestClient]:
    """A TestClient wired to an isolated in-memory SQLite database and a fake
    (in-process) Redis server — same StaticPool-backed sqlite shape as
    platform-registry's own test_api.py fixture.
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
    statuses = {c["status"] for c in response.json()}
    assert statuses == {"live", "partial", "planned"}


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
