"""Tests for the caller-identity resolution dependency (auth + entitlement check)."""

from __future__ import annotations

import pytest
from ai_circus_shared.auth import Identity, TokenValidationError
from ai_circus_shared.entitlements import EntitlementDeniedError
from fastapi import HTTPException

import prediction.core.identity as identity_module


class FakeConfig:
    """Minimal stand-in for EnvConfig, covering the fields resolve_identity() reads."""

    def __init__(self, *, auth_disabled: str = "false") -> None:
        """Populate fixed configuration values for identity resolution tests."""
        self.AUTH_DISABLED = auth_disabled
        self.DEV_ORG_ID = "demo"
        self.SCENARIO_SLUG = "churn"
        self.LOGTO_ISSUER = "http://logto.localhost/oidc"
        self.LOGTO_API_RESOURCE_INDICATOR = "https://api.ai-circus-framework.local"
        self.LOGTO_JWKS_URL = "http://logto.localhost/oidc/jwks"
        self.PLATFORM_REGISTRY_URL = "http://platform-registry:8000"


def test_auth_disabled_returns_fixed_dev_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTH_DISABLED=true bypasses token validation entirely."""
    monkeypatch.setattr(identity_module, "get_env_config", lambda: FakeConfig(auth_disabled="true"))
    monkeypatch.setattr(identity_module.PlatformRegistryClient, "check_entitlement", lambda self, **_kwargs: None)

    identity = identity_module.resolve_identity(authorization=None)

    assert identity.org_id == "demo"
    assert identity.roles == frozenset({"scenario:churn"})


def test_missing_authorization_header_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Authorization header (and auth enabled) is rejected before any token parsing."""
    monkeypatch.setattr(identity_module, "get_env_config", lambda: FakeConfig())

    with pytest.raises(HTTPException) as exc_info:
        identity_module.resolve_identity(authorization=None)

    assert exc_info.value.status_code == 401


def test_invalid_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token that fails validation is rejected with 401, not a raw exception."""
    monkeypatch.setattr(identity_module, "get_env_config", lambda: FakeConfig())

    def raise_invalid(*_args: object, **_kwargs: object) -> Identity:
        raise TokenValidationError("bad signature")

    monkeypatch.setattr(identity_module, "validate_token", raise_invalid)

    with pytest.raises(HTTPException) as exc_info:
        identity_module.resolve_identity(authorization="Bearer bad-token")

    assert exc_info.value.status_code == 401


def test_entitlement_denied_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """A validly authenticated caller whose org lacks the scenario entitlement gets 403."""
    monkeypatch.setattr(identity_module, "get_env_config", lambda: FakeConfig())
    monkeypatch.setattr(
        identity_module,
        "validate_token",
        lambda *_a, **_kw: Identity(subject="user-1", org_id="org-1", roles=frozenset()),
    )

    def deny(self: object, **_kwargs: object) -> None:
        raise EntitlementDeniedError("not entitled")

    monkeypatch.setattr(identity_module.PlatformRegistryClient, "check_entitlement", deny)

    with pytest.raises(HTTPException) as exc_info:
        identity_module.resolve_identity(authorization="Bearer good-token")

    assert exc_info.value.status_code == 403


def test_entitled_caller_resolves_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """A validly authenticated, entitled caller resolves to their identity."""
    monkeypatch.setattr(identity_module, "get_env_config", lambda: FakeConfig())
    monkeypatch.setattr(
        identity_module,
        "validate_token",
        lambda *_a, **_kw: Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})),
    )
    monkeypatch.setattr(identity_module.PlatformRegistryClient, "check_entitlement", lambda self, **_kwargs: None)

    identity = identity_module.resolve_identity(authorization="Bearer good-token")

    assert identity.org_id == "org-1"
