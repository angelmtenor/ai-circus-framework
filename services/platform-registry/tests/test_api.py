"""Tests for the entitlement/scenario-metadata API."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from platform_registry.api import require_admin
from platform_registry.app import app
from platform_registry.core.db import get_session
from platform_registry.core.models import Base, Scenario
from tests.conftest import FakeSecret


@pytest.fixture
def client() -> Generator[TestClient]:
    """A TestClient wired to an isolated in-memory SQLite database, pre-seeded with one scenario."""
    # StaticPool keeps a single shared connection alive for this in-memory sqlite
    # database — otherwise each new connection would see a separate, empty database.
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as setup_session:
        setup_session.add(
            Scenario(
                slug="churn",
                kind="tabular_ml",
                title="Customer Churn Prediction",
                description="Predicts churn.",
                icon="📉",
                role_required="scenario:churn",
            )
        )
        setup_session.commit()

    def override_get_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[require_admin] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_healthz(client: TestClient) -> None:
    """/healthz reports ok."""
    assert client.get("/healthz").json() == {"status": "ok"}


def test_check_entitlement_denied_by_default(client: TestClient) -> None:
    """An org with no granted entitlement gets a 404."""
    response = client.get("/entitlements/org-1/churn")
    assert response.status_code == 404


def test_grant_then_check_then_list_then_revoke(client: TestClient) -> None:
    """The full grant -> check -> list -> revoke lifecycle for one org/scenario pair."""
    assert client.put("/entitlements/org-1/churn").status_code == 204
    assert client.get("/entitlements/org-1/churn").json() == {"entitled": True}

    scenarios = client.get("/entitlements/org-1").json()
    assert [s["slug"] for s in scenarios] == ["churn"]

    assert client.delete("/entitlements/org-1/churn").status_code == 204
    assert client.get("/entitlements/org-1/churn").status_code == 404


def test_grant_unknown_scenario_returns_404(client: TestClient) -> None:
    """Granting an entitlement for a scenario slug that doesn't exist is rejected."""
    response = client.put("/entitlements/org-1/does-not-exist")
    assert response.status_code == 404


class _FakeAdminConfig:
    """Stand-in for EnvConfig exposing only what `require_admin` reads."""

    def __init__(self, admin_api_key: str = "test-admin-key") -> None:
        self.ADMIN_API_KEY = FakeSecret(admin_api_key)


@pytest.fixture
def unauthenticated_client(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Same wiring as `client`, but with the real `require_admin` dependency restored,
    backed by a fake config so the admin-gate check doesn't need every mandatory env var.
    """
    del app.dependency_overrides[require_admin]
    monkeypatch.setattr("platform_registry.api.get_env_config", lambda: _FakeAdminConfig())
    return client


def test_grant_entitlement_requires_admin_token(unauthenticated_client: TestClient) -> None:
    """Without an admin bearer token, granting an entitlement is rejected, not silently allowed."""
    response = unauthenticated_client.put("/entitlements/org-1/churn")
    assert response.status_code == 401


def test_revoke_entitlement_requires_admin_token(unauthenticated_client: TestClient) -> None:
    """Without an admin bearer token, revoking an entitlement is rejected, not silently allowed."""
    response = unauthenticated_client.delete("/entitlements/org-1/churn")
    assert response.status_code == 401


def test_grant_entitlement_succeeds_with_admin_token(unauthenticated_client: TestClient) -> None:
    """The real admin bearer token still authorizes the mutation."""
    response = unauthenticated_client.put(
        "/entitlements/org-1/churn", headers={"Authorization": "Bearer test-admin-key"}
    )
    assert response.status_code == 204


def test_check_entitlement_does_not_require_admin_token(unauthenticated_client: TestClient) -> None:
    """The read-only entitlement check stays open to every backend service, unauthenticated."""
    response = unauthenticated_client.get("/entitlements/org-1/churn")
    assert response.status_code == 404


def test_get_active_llm_model_404s_before_any_is_set(client: TestClient) -> None:
    """No llm_settings row yet (fresh DB, no seeding) is a 404, not a made-up default."""
    response = client.get("/llm-settings/active-model")
    assert response.status_code == 404


def test_set_then_get_active_llm_model(client: TestClient) -> None:
    """Setting the active model persists it for subsequent reads — no restart involved."""
    response = client.put("/llm-settings/active-model", json={"model_name": "gemini-flash"})
    assert response.status_code == 200
    assert response.json() == {"model_name": "gemini-flash"}

    assert client.get("/llm-settings/active-model").json() == {"model_name": "gemini-flash"}


def test_set_active_llm_model_rejects_unrouted_model_name(client: TestClient) -> None:
    """A model_name not present in litellm_config.yaml's routing table (llm_settings.PROVIDERS) is rejected."""
    response = client.put("/llm-settings/active-model", json={"model_name": "not-a-real-model"})
    assert response.status_code == 400


def test_set_active_llm_model_updates_in_place(client: TestClient) -> None:
    """Setting the model twice updates the same singleton row rather than erroring."""
    client.put("/llm-settings/active-model", json={"model_name": "gemini-flash"})
    response = client.put("/llm-settings/active-model", json={"model_name": "groq-llama"})

    assert response.status_code == 200
    assert response.json() == {"model_name": "groq-llama"}
