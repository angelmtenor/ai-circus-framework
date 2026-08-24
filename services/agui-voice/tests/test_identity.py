"""Tests for agui-voice's core/identity.py — a thin wrapper around
ai_circus_shared.auth.resolve_caller_identity. Its own logic (AUTH_DISABLED bypass,
ADMIN_API_KEY bypass, Logto validation, entitlement enforcement) is tested once,
directly, in libs/shared/tests/test_auth.py — this file only covers what's specific
to this wrapper: adapting SecretStr, translating domain exceptions to HTTPException
for the HTTP route, and letting the WebSocket route see the raw domain exceptions.
"""

from __future__ import annotations

import pytest
from ai_circus_shared.auth import AuthSettingsAdapter, Identity, TokenValidationError
from ai_circus_shared.entitlements import EntitlementDeniedError
from fastapi import HTTPException
from pydantic import SecretStr

import agui_voice.core.identity as identity_module


class FakeConfig:
    """Minimal stand-in for EnvConfig, covering the fields identity resolution reads."""

    def __init__(self) -> None:
        """Populate fixed configuration values for identity resolution tests."""
        self.AUTH_DISABLED = "false"
        self.DEV_ORG_ID = "demo"
        self.LOGTO_ISSUER = "http://logto.localhost/oidc"
        self.LOGTO_API_RESOURCE_INDICATOR = "https://api.ai-circus-framework.local"
        self.LOGTO_JWKS_URL = "http://logto.localhost/oidc/jwks"
        self.ADMIN_API_KEY = SecretStr("ai-circus-2026")
        self.ENGINEERING_DEMO_API_KEY = SecretStr("ai-circus-engineering-2026")
        self.PLATFORM_REGISTRY_URL = "http://platform-registry:8000"


def test_admin_api_key_is_unwrapped_from_secretstr(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SecretStr ADMIN_API_KEY is passed to the shared function as a plain str."""
    monkeypatch.setattr(identity_module, "get_env_config", FakeConfig)
    captured: dict[str, object] = {}

    def fake_resolve(**kwargs: object) -> Identity:
        captured.update(kwargs)
        return Identity(subject="admin", org_id="admin", roles=frozenset())

    monkeypatch.setattr(identity_module, "resolve_caller_identity", fake_resolve)

    identity_module.resolve_identity_from_token("churn", "Bearer ai-circus-2026")

    settings = captured["settings"]
    assert isinstance(settings, AuthSettingsAdapter)
    assert settings.ADMIN_API_KEY == "ai-circus-2026"
    assert captured["scenario_slug"] == "churn"


def test_resolve_identity_translates_token_validation_error_to_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """The HTTP-facing resolve_identity() translates TokenValidationError to a 401."""
    monkeypatch.setattr(identity_module, "get_env_config", FakeConfig)

    def raise_invalid(**_kwargs: object) -> Identity:
        raise TokenValidationError("bad token")

    monkeypatch.setattr(identity_module, "resolve_caller_identity", raise_invalid)

    with pytest.raises(HTTPException) as exc_info:
        identity_module.resolve_identity(scenario_slug="churn", authorization=None)

    assert exc_info.value.status_code == 401


def test_resolve_identity_translates_entitlement_denied_to_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """The HTTP-facing resolve_identity() translates EntitlementDeniedError to a 403."""
    monkeypatch.setattr(identity_module, "get_env_config", FakeConfig)

    def raise_denied(**_kwargs: object) -> Identity:
        raise EntitlementDeniedError("not entitled")

    monkeypatch.setattr(identity_module, "resolve_caller_identity", raise_denied)

    with pytest.raises(HTTPException) as exc_info:
        identity_module.resolve_identity(scenario_slug="churn", authorization="Bearer good-token")

    assert exc_info.value.status_code == 403


def test_resolve_identity_from_token_lets_domain_exceptions_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """The WebSocket-facing helper raises the raw domain exception (no HTTPException translation),
    so api/ws.py can pick its own close code per exception type.
    """
    monkeypatch.setattr(identity_module, "get_env_config", FakeConfig)

    def raise_invalid(**_kwargs: object) -> Identity:
        raise TokenValidationError("bad token")

    monkeypatch.setattr(identity_module, "resolve_caller_identity", raise_invalid)

    with pytest.raises(TokenValidationError):
        identity_module.resolve_identity_from_token("churn", None)


def test_entitled_caller_resolves_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful resolution passes the identity straight through."""
    monkeypatch.setattr(identity_module, "get_env_config", FakeConfig)
    monkeypatch.setattr(
        identity_module,
        "resolve_caller_identity",
        lambda **_kw: Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})),
    )

    identity = identity_module.resolve_identity(scenario_slug="churn", authorization="Bearer good-token")

    assert identity.org_id == "org-1"
