"""Tests for rag-agent's core/identity.py — a thin FastAPI wrapper around
ai_circus_shared.auth.resolve_caller_identity. Its own logic (AUTH_DISABLED bypass,
ADMIN_API_KEY bypass, Logto validation, entitlement enforcement) is tested once,
directly, in libs/shared/tests/test_auth.py — this file only covers what's specific
to this wrapper: adapting SecretStr and translating domain exceptions to HTTPException.
"""

from __future__ import annotations

import pytest
from ai_circus_shared.auth import AuthSettingsAdapter, Identity, TokenValidationError
from ai_circus_shared.entitlements import EntitlementDeniedError
from fastapi import HTTPException
from pydantic import SecretStr

import rag_agent.core.identity as identity_module


class FakeConfig:
    """Minimal stand-in for EnvConfig, covering the fields resolve_identity() reads."""

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

    identity_module.resolve_identity(scenario_slug="docs_rag", authorization="Bearer ai-circus-2026")

    settings = captured["settings"]
    assert isinstance(settings, AuthSettingsAdapter)
    assert settings.ADMIN_API_KEY == "ai-circus-2026"
    assert captured["scenario_slug"] == "docs_rag"


def test_engineering_demo_api_key_is_unwrapped_from_secretstr(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SecretStr ENGINEERING_DEMO_API_KEY is passed to the shared function as a plain str."""
    monkeypatch.setattr(identity_module, "get_env_config", FakeConfig)
    captured: dict[str, object] = {}

    def fake_resolve(**kwargs: object) -> Identity:
        captured.update(kwargs)
        return Identity(subject="engineering-demo", org_id="engineering-demo", roles=frozenset())

    monkeypatch.setattr(identity_module, "resolve_caller_identity", fake_resolve)

    identity_module.resolve_identity(scenario_slug="mpm", authorization="Bearer ai-circus-engineering-2026")

    settings = captured["settings"]
    assert isinstance(settings, AuthSettingsAdapter)
    assert settings.ENGINEERING_DEMO_API_KEY == "ai-circus-engineering-2026"


def test_token_validation_error_becomes_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """TokenValidationError from the shared function is translated to a 401."""
    monkeypatch.setattr(identity_module, "get_env_config", FakeConfig)

    def raise_invalid(**_kwargs: object) -> Identity:
        raise TokenValidationError("bad token")

    monkeypatch.setattr(identity_module, "resolve_caller_identity", raise_invalid)

    with pytest.raises(HTTPException) as exc_info:
        identity_module.resolve_identity(scenario_slug="docs_rag", authorization=None)

    assert exc_info.value.status_code == 401


def test_entitlement_denied_becomes_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """EntitlementDeniedError from the shared function is translated to a 403."""
    monkeypatch.setattr(identity_module, "get_env_config", FakeConfig)

    def raise_denied(**_kwargs: object) -> Identity:
        raise EntitlementDeniedError("not entitled")

    monkeypatch.setattr(identity_module, "resolve_caller_identity", raise_denied)

    with pytest.raises(HTTPException) as exc_info:
        identity_module.resolve_identity(scenario_slug="docs_rag", authorization="Bearer good-token")

    assert exc_info.value.status_code == 403


def test_entitled_caller_resolves_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful resolution passes the identity straight through."""
    monkeypatch.setattr(identity_module, "get_env_config", FakeConfig)
    monkeypatch.setattr(
        identity_module,
        "resolve_caller_identity",
        lambda **_kw: Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:docs_rag"})),
    )

    identity = identity_module.resolve_identity(scenario_slug="docs_rag", authorization="Bearer good-token")

    assert identity.org_id == "org-1"
