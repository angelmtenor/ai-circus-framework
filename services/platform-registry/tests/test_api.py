"""Tests for the entitlement/scenario-metadata API."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from platform_registry.api import require_admin, require_authenticated, require_org_match
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
                industry="banking_finance",
            )
        )
        setup_session.commit()

    def override_get_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[require_admin] = lambda: None
    app.dependency_overrides[require_org_match] = lambda: None
    app.dependency_overrides[require_authenticated] = lambda: None
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
    """Stand-in for EnvConfig exposing what `require_admin`/`require_org_match`/
    `verify_engineering_demo_key` read.
    """

    def __init__(
        self,
        admin_api_key: str = "test-admin-key",
        engineering_demo_api_key: str | None = "test-engineering-demo-key",
        auth_disabled: str = "false",
        dev_org_id: str = "demo",
    ) -> None:
        self.ADMIN_API_KEY = FakeSecret(admin_api_key)
        self.ENGINEERING_DEMO_API_KEY = FakeSecret(engineering_demo_api_key) if engineering_demo_api_key else None
        self.AUTH_DISABLED = auth_disabled
        self.DEV_ORG_ID = dev_org_id
        # Logto unconfigured here (no test exercises a real Logto token) — resolve_org_identity
        # raises RuntimeError if this path is ever reached without AUTH_DISABLED/an admin key.
        self.LOGTO_ISSUER = None
        self.LOGTO_API_RESOURCE_INDICATOR = None
        self.LOGTO_JWKS_URL = None


@pytest.fixture
def unauthenticated_client(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Same wiring as `client`, but with the real `require_admin` dependency restored,
    backed by a fake config so the admin-gate check doesn't need every mandatory env var.
    """
    del app.dependency_overrides[require_admin]
    monkeypatch.setattr("platform_registry.api.get_env_config", lambda: _FakeAdminConfig())
    return client


@pytest.fixture
def org_match_client(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Same wiring as `client`, but with the real `require_org_match` dependency
    restored — for testing GET /entitlements/{org_id}'s own auth gate specifically.
    """
    del app.dependency_overrides[require_org_match]
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
    """The org/scenario entitlement check stays open to every backend service,
    unauthenticated — it's called server-to-server, never from a browser (see
    require_org_match's docstring for why GET /entitlements/{org_id} is different).
    """
    response = unauthenticated_client.get("/entitlements/org-1/churn")
    assert response.status_code == 404


def test_list_entitled_scenarios_rejects_unauthenticated_caller(org_match_client: TestClient) -> None:
    """No Authorization header at all is rejected before any org comparison."""
    response = org_match_client.get("/entitlements/org-1")
    assert response.status_code == 401


def test_list_entitled_scenarios_rejects_mismatched_org(org_match_client: TestClient) -> None:
    """A caller authenticated as one org can't list a DIFFERENT org's scenario catalog —
    the cross-tenant metadata disclosure this dependency exists to close.
    """
    response = org_match_client.get("/entitlements/org-1", headers={"Authorization": "Bearer test-admin-key"})
    assert response.status_code == 403


def test_list_entitled_scenarios_allows_matching_org(org_match_client: TestClient) -> None:
    """The admin bearer token resolves to the 'admin' org, which may list ITS OWN catalog."""
    response = org_match_client.get("/entitlements/admin", headers={"Authorization": "Bearer test-admin-key"})
    assert response.status_code == 200


def test_verify_engineering_demo_key_rejects_missing_token(unauthenticated_client: TestClient) -> None:
    """No Authorization header at all is rejected."""
    response = unauthenticated_client.get("/auth/verify-engineering-demo-key")
    assert response.status_code == 401


def test_verify_engineering_demo_key_rejects_wrong_token(unauthenticated_client: TestClient) -> None:
    """A bearer token that doesn't match ENGINEERING_DEMO_API_KEY is rejected."""
    response = unauthenticated_client.get(
        "/auth/verify-engineering-demo-key", headers={"Authorization": "Bearer not-the-demo-key"}
    )
    assert response.status_code == 401


def test_verify_engineering_demo_key_rejects_the_admin_key(unauthenticated_client: TestClient) -> None:
    """The admin key doesn't also work here — the two credentials are deliberately distinct."""
    response = unauthenticated_client.get(
        "/auth/verify-engineering-demo-key", headers={"Authorization": "Bearer test-admin-key"}
    )
    assert response.status_code == 401


def test_verify_engineering_demo_key_accepts_the_real_key(unauthenticated_client: TestClient) -> None:
    """The configured ENGINEERING_DEMO_API_KEY is accepted."""
    response = unauthenticated_client.get(
        "/auth/verify-engineering-demo-key", headers={"Authorization": "Bearer test-engineering-demo-key"}
    )
    assert response.status_code == 200
    assert response.json() == {"valid": True}


def test_verify_engineering_demo_key_rejects_everything_when_unconfigured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENGINEERING_DEMO_API_KEY is optional — when unset, no bearer token can match it."""
    monkeypatch.setattr("platform_registry.api.get_env_config", lambda: _FakeAdminConfig(engineering_demo_api_key=None))
    response = client.get(
        "/auth/verify-engineering-demo-key", headers={"Authorization": "Bearer test-engineering-demo-key"}
    )
    assert response.status_code == 401


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


def test_get_active_voice_settings_404s_before_any_is_set(client: TestClient) -> None:
    """No voice_settings row yet (fresh DB, no seeding) is a 404, not a made-up default."""
    response = client.get("/voice-settings/active")
    assert response.status_code == 404


def test_set_then_get_active_voice_settings(client: TestClient) -> None:
    """Setting the active STT/TTS provider persists it for subsequent reads — no restart involved."""
    response = client.put("/voice-settings/active", json={"stt_provider": "deepgram", "tts_provider": "elevenlabs"})
    assert response.status_code == 200
    assert response.json() == {"stt_provider": "deepgram", "tts_provider": "elevenlabs"}

    assert client.get("/voice-settings/active").json() == {"stt_provider": "deepgram", "tts_provider": "elevenlabs"}


def test_set_active_voice_settings_rejects_unknown_stt_provider(client: TestClient) -> None:
    """An stt_provider outside agui-voice's known set is rejected."""
    response = client.put(
        "/voice-settings/active", json={"stt_provider": "not-a-real-provider", "tts_provider": "piper"}
    )
    assert response.status_code == 400


def test_set_active_voice_settings_rejects_unknown_tts_provider(client: TestClient) -> None:
    """A tts_provider outside agui-voice's known set is rejected."""
    response = client.put(
        "/voice-settings/active", json={"stt_provider": "whisper", "tts_provider": "not-a-real-provider"}
    )
    assert response.status_code == 400


def test_set_active_voice_settings_updates_in_place(client: TestClient) -> None:
    """Setting voice settings twice updates the same singleton row rather than erroring."""
    client.put("/voice-settings/active", json={"stt_provider": "whisper", "tts_provider": "piper"})
    response = client.put("/voice-settings/active", json={"stt_provider": "deepgram", "tts_provider": "cartesia"})

    assert response.status_code == 200
    assert response.json() == {"stt_provider": "deepgram", "tts_provider": "cartesia"}


def test_extract_document_txt(client: TestClient) -> None:
    """POST /documents/extract passes .txt content straight through."""
    response = client.post(
        "/documents/extract",
        files={"file": ("notes.txt", b"hello from a plain text attachment", "text/plain")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "text"
    assert body["text"] == "hello from a plain text attachment"
    assert body["truncated"] is False
    assert body["used_ocr"] is False


def test_extract_document_rejects_unsupported_extension(client: TestClient) -> None:
    """An unrecognized extension is a 415, not a silent empty extraction."""
    response = client.post("/documents/extract", files={"file": ("archive.zip", b"PK\x03\x04", "application/zip")})
    assert response.status_code == 415
