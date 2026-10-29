"""Tests for reporting gateway budget changes to platform-registry's admin audit trail."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import fakeredis
import httpx
import pytest
from ai_circus_shared.document_store import Base
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from data_platform_manager.api import get_document_session as api_get_document_session
from data_platform_manager.api import require_admin
from data_platform_manager.app import app
from data_platform_manager.core import governance_audit
from data_platform_manager.core.cache_client import get_client


class _Config:
    GOVERNANCE_AUDIT_ENABLED = "true"
    PLATFORM_REGISTRY_URL = "http://platform-registry:8000"


@pytest.fixture
def client() -> Generator[TestClient]:
    """A client over an in-memory database and fake Valkey, with admin auth bypassed."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    fake_redis = fakeredis.FakeStrictRedis(decode_responses=True)

    def override_get_session() -> Generator:
        with sessions() as session:
            yield session

    app.dependency_overrides[api_get_document_session] = override_get_session
    app.dependency_overrides[get_client] = lambda: fake_redis
    app.dependency_overrides[require_admin] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def posts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Audit enabled, with httpx.post captured instead of sent."""
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        sent.append({"url": url, **kwargs})
        return httpx.Response(201, request=httpx.Request("POST", url))

    monkeypatch.setattr(governance_audit, "_config", lambda: _Config())
    monkeypatch.setattr(governance_audit.httpx, "post", fake_post)
    return sent


def test_setting_and_changing_a_budget_is_reported_with_before_and_after(
    client: TestClient, posts: list[dict[str, Any]]
) -> None:
    headers = {"Authorization": "Bearer admin-token"}
    assert client.put("/gateway/budgets/org-1", json={"monthly_cap_usd": 25.0}, headers=headers).status_code == 200
    assert client.put("/gateway/budgets/org-1", json={"monthly_cap_usd": 40.0}, headers=headers).status_code == 200

    first, second = posts
    assert first["url"] == "http://platform-registry:8000/governance/audit"
    assert first["headers"] == {"Authorization": "Bearer admin-token"}
    assert first["json"] == {
        "action": "gateway.budget.set",
        "target_type": "budget",
        "target_id": "org-1",
        "org_id": "org-1",
        "before": None,
        "after": {"monthly_cap_usd": 25.0},
    }
    assert second["json"]["before"] == {"monthly_cap_usd": 25.0}
    assert second["json"]["after"] == {"monthly_cap_usd": 40.0}


def test_deleting_a_budget_is_reported_but_deleting_nothing_is_not(
    client: TestClient, posts: list[dict[str, Any]]
) -> None:
    headers = {"Authorization": "Bearer admin-token"}
    client.put("/gateway/budgets/org-1", json={"monthly_cap_usd": 25.0}, headers=headers)
    posts.clear()

    assert client.delete("/gateway/budgets/org-1", headers=headers).json() == {"deleted": True}
    assert client.delete("/gateway/budgets/org-1", headers=headers).json() == {"deleted": False}

    (deleted,) = posts
    assert deleted["json"]["action"] == "gateway.budget.deleted"
    assert deleted["json"]["before"] == {"monthly_cap_usd": 25.0} and deleted["json"]["after"] is None


def test_with_the_flag_off_nothing_is_sent_and_budgets_work_as_before(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Off:
        GOVERNANCE_AUDIT_ENABLED = None

    def must_not_be_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no audit call expected while the flag is off")

    monkeypatch.setattr(governance_audit, "_config", lambda: Off())
    monkeypatch.setattr(governance_audit.httpx, "post", must_not_be_called)

    assert client.put("/gateway/budgets/org-1", json={"monthly_cap_usd": 25.0}).status_code == 200
    assert client.get("/gateway/budgets").json()[0]["monthly_cap_usd"] == pytest.approx(25.0)
    assert client.delete("/gateway/budgets/org-1").json() == {"deleted": True}


def test_a_registry_outage_never_blocks_the_budget_change(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def down(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("registry down")

    monkeypatch.setattr(governance_audit, "_config", lambda: _Config())
    monkeypatch.setattr(governance_audit.httpx, "post", down)

    response = client.put(
        "/gateway/budgets/org-1", json={"monthly_cap_usd": 25.0}, headers={"Authorization": "Bearer t"}
    )

    assert response.status_code == 200
    assert client.get("/gateway/budgets").json()[0]["monthly_cap_usd"] == pytest.approx(25.0)


def test_a_registry_error_status_counts_as_not_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(governance_audit, "_config", lambda: _Config())
    monkeypatch.setattr(
        governance_audit.httpx,
        "post",
        lambda url, **kw: httpx.Response(401, request=httpx.Request("POST", url)),
    )
    assert not governance_audit.record_budget_change(
        authorization="Bearer t", action="gateway.budget.set", org_id="o", before=None, after={"monthly_cap_usd": 1.0}
    )


def test_missing_credentials_or_url_skips_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(governance_audit, "_config", lambda: _Config())
    monkeypatch.setattr(governance_audit.httpx, "post", lambda *a, **k: pytest.fail("must not post"))
    assert not governance_audit.record_budget_change(
        authorization=None, action="gateway.budget.set", org_id="o", before=None, after=None
    )

    class NoUrl:
        GOVERNANCE_AUDIT_ENABLED = "true"
        PLATFORM_REGISTRY_URL = None

    monkeypatch.setattr(governance_audit, "_config", lambda: NoUrl())
    assert not governance_audit.record_budget_change(
        authorization="Bearer t", action="gateway.budget.set", org_id="o", before=None, after=None
    )
