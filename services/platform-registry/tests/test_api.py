"""Tests for the entitlement/scenario-metadata API."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from platform_registry.app import app
from platform_registry.core.db import get_session
from platform_registry.core.models import Base, Scenario


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
