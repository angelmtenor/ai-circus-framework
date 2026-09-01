"""Tests for resolve_caller_identity — the consolidated auth+entitlement dependency
every service's core/identity.py wraps."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ai_circus_shared import auth as auth_module
from ai_circus_shared.auth import (
    ADMIN_ORG_ID,
    ENGINEERING_DEMO_ORG_ID,
    Identity,
    TokenValidationError,
    resolve_caller_identity,
    resolve_org_identity,
)
from ai_circus_shared.entitlements import EntitlementDeniedError


class TestExtractOrgId:
    """`_extract_org_id` parses Keycloak's alias-keyed `organization` claim shape."""

    def test_reads_id_from_first_org_entry(self) -> None:
        claims = {"organization": {"acme-corp": {"id": "org-1", "groups": ["/Engineering"]}}}
        assert auth_module._extract_org_id(claims) == "org-1"

    def test_missing_claim_returns_none(self) -> None:
        assert auth_module._extract_org_id({}) is None

    def test_empty_claim_returns_none(self) -> None:
        assert auth_module._extract_org_id({"organization": {}}) is None


def test_validate_token_reads_nested_realm_roles_and_org_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keycloak nests roles under realm_access.roles, not a flat `roles` array."""
    claims = {
        "sub": "user-1",
        "realm_access": {"roles": ["scenario:churn", "offline_access"]},
        "organization": {"acme-corp": {"id": "org-1"}},
    }
    monkeypatch.setattr(auth_module.jwt, "decode", lambda *_a, **_kw: claims)
    monkeypatch.setattr(
        auth_module,
        "_jwks_client",
        lambda _url: type("FakeJwksClient", (), {"get_signing_key_from_jwt": lambda self, _t: type("Key", (), {"key": "k"})()})(),
    )

    identity = auth_module.validate_token(
        "irrelevant", issuer="http://keycloak.localhost/realms/ai-circus", audience="aud", jwks_url="http://jwks"
    )

    assert identity.subject == "user-1"
    assert identity.org_id == "org-1"
    assert identity.roles == frozenset({"scenario:churn", "offline_access"})


@dataclass
class FakeSettings:
    """Minimal stand-in for a service's EnvConfig, covering AuthSettings' fields."""

    AUTH_DISABLED: str = "false"
    DEV_ORG_ID: str = "demo"
    KEYCLOAK_ISSUER: str | None = "http://keycloak.localhost/realms/ai-circus"
    KEYCLOAK_AUDIENCE: str | None = "https://api.ai-circus-framework.local"
    KEYCLOAK_JWKS_URL: str | None = "http://keycloak.localhost/realms/ai-circus/protocol/openid-connect/certs"
    ADMIN_API_KEY: str | None = "ai-circus-2026"
    ENGINEERING_DEMO_API_KEY: str | None = "ai-circus-engineering-2026"
    PLATFORM_REGISTRY_URL: str = "http://platform-registry:8000"


def _allow_entitlement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module.PlatformRegistryClient, "check_entitlement", lambda self, **_kwargs: None)


def _deny_entitlement(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(self: object, **_kwargs: object) -> None:
        raise EntitlementDeniedError("not entitled")

    monkeypatch.setattr(auth_module.PlatformRegistryClient, "check_entitlement", deny)


def test_auth_disabled_returns_fixed_dev_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTH_DISABLED=true bypasses token validation entirely."""
    _allow_entitlement(monkeypatch)

    identity = resolve_caller_identity(
        authorization=None, scenario_slug="churn", settings=FakeSettings(AUTH_DISABLED="true")
    )

    assert identity.org_id == "demo"
    assert identity.roles == frozenset({"scenario:churn"})


def test_admin_api_key_resolves_to_admin_org(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exact ADMIN_API_KEY bearer match resolves to ADMIN_ORG_ID, not a Keycloak token."""
    _allow_entitlement(monkeypatch)

    identity = resolve_caller_identity(
        authorization="Bearer ai-circus-2026", scenario_slug="mpm", settings=FakeSettings()
    )

    assert identity.org_id == ADMIN_ORG_ID
    assert identity.subject == "admin"


def test_admin_api_key_still_goes_through_entitlement_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin access is a real entitlement, not a bypass of check_entitlement — deny it and confirm 403-equivalent."""
    _deny_entitlement(monkeypatch)

    with pytest.raises(EntitlementDeniedError):
        resolve_caller_identity(authorization="Bearer ai-circus-2026", scenario_slug="mpm", settings=FakeSettings())


def test_engineering_demo_api_key_resolves_to_engineering_demo_org(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exact ENGINEERING_DEMO_API_KEY bearer match resolves to ENGINEERING_DEMO_ORG_ID, not admin/Keycloak."""
    _allow_entitlement(monkeypatch)

    identity = resolve_caller_identity(
        authorization="Bearer ai-circus-engineering-2026", scenario_slug="mpm", settings=FakeSettings()
    )

    assert identity.org_id == ENGINEERING_DEMO_ORG_ID
    assert identity.subject == "engineering-demo"


def test_engineering_demo_api_key_still_goes_through_entitlement_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Engineering-demo access is a real (scoped) entitlement, not a bypass — deny it and confirm 403-equivalent."""
    _deny_entitlement(monkeypatch)

    with pytest.raises(EntitlementDeniedError):
        resolve_caller_identity(
            authorization="Bearer ai-circus-engineering-2026", scenario_slug="churn", settings=FakeSettings()
        )


def test_no_engineering_demo_api_key_configured_skips_that_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A falsy ENGINEERING_DEMO_API_KEY never matches — falls through to (here, mocked) Keycloak validation instead."""

    def raise_invalid(*_args: object, **_kwargs: object) -> Identity:
        raise TokenValidationError("bad signature")

    monkeypatch.setattr(auth_module, "validate_token", raise_invalid)

    with pytest.raises(TokenValidationError):
        resolve_caller_identity(
            authorization="Bearer ai-circus-engineering-2026",
            scenario_slug="churn",
            settings=FakeSettings(ADMIN_API_KEY=None, ENGINEERING_DEMO_API_KEY=None),
        )


def test_wrong_admin_api_key_falls_through_to_keycloak_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bearer token that doesn't match ADMIN_API_KEY is treated as a (here, invalid) Keycloak token."""

    def raise_invalid(*_args: object, **_kwargs: object) -> Identity:
        raise TokenValidationError("bad signature")

    monkeypatch.setattr(auth_module, "validate_token", raise_invalid)

    with pytest.raises(TokenValidationError):
        resolve_caller_identity(
            authorization="Bearer not-the-admin-key", scenario_slug="churn", settings=FakeSettings()
        )


def test_missing_authorization_header_raises_token_validation_error() -> None:
    """No Authorization header (and auth enabled, no admin key match) is rejected before any token parsing."""
    with pytest.raises(TokenValidationError):
        resolve_caller_identity(authorization=None, scenario_slug="churn", settings=FakeSettings())


def test_entitlement_denied_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A validly authenticated caller whose org lacks the scenario entitlement raises EntitlementDeniedError."""
    monkeypatch.setattr(
        auth_module,
        "validate_token",
        lambda *_a, **_kw: Identity(subject="user-1", org_id="org-1", roles=frozenset()),
    )
    _deny_entitlement(monkeypatch)

    with pytest.raises(EntitlementDeniedError):
        resolve_caller_identity(authorization="Bearer good-token", scenario_slug="churn", settings=FakeSettings())


def test_entitled_caller_resolves_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """A validly authenticated, entitled caller resolves to their identity."""
    monkeypatch.setattr(
        auth_module,
        "validate_token",
        lambda *_a, **_kw: Identity(subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})),
    )
    _allow_entitlement(monkeypatch)

    identity = resolve_caller_identity(
        authorization="Bearer good-token", scenario_slug="churn", settings=FakeSettings()
    )

    assert identity.org_id == "org-1"


def test_token_with_no_org_claim_raises_token_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A validated token with no organization claim is rejected before the entitlement check."""
    monkeypatch.setattr(
        auth_module, "validate_token", lambda *_a, **_kw: Identity(subject="user-1", org_id=None, roles=frozenset())
    )

    with pytest.raises(TokenValidationError):
        resolve_caller_identity(authorization="Bearer good-token", scenario_slug="churn", settings=FakeSettings())


def test_no_admin_api_key_configured_skips_admin_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A falsy ADMIN_API_KEY never matches — falls through to (here, mocked) Keycloak validation instead."""

    def raise_invalid(*_args: object, **_kwargs: object) -> Identity:
        raise TokenValidationError("bad signature")

    monkeypatch.setattr(auth_module, "validate_token", raise_invalid)

    with pytest.raises(TokenValidationError):
        resolve_caller_identity(
            authorization="Bearer anything", scenario_slug="churn", settings=FakeSettings(ADMIN_API_KEY=None)
        )


def test_keycloak_not_configured_raises_runtime_error() -> None:
    """AUTH_DISABLED=false, no matching admin key, and Keycloak unconfigured is a server misconfiguration."""
    with pytest.raises(RuntimeError):
        resolve_caller_identity(
            authorization="Bearer anything",
            scenario_slug="churn",
            settings=FakeSettings(ADMIN_API_KEY=None, KEYCLOAK_ISSUER=None),
        )


class TestResolveOrgIdentity:
    """resolve_org_identity: same four resolution paths as resolve_caller_identity, but
    with no trailing check_entitlement call — for platform-registry's own
    `/entitlements/{org_id}` reads, where that call would recurse into its own API."""

    def test_auth_disabled_returns_fixed_dev_identity(self) -> None:
        identity = resolve_org_identity(authorization=None, settings=FakeSettings(AUTH_DISABLED="true"))
        assert identity.org_id == "demo"

    def test_admin_api_key_resolves_to_admin_org(self) -> None:
        identity = resolve_org_identity(authorization="Bearer ai-circus-2026", settings=FakeSettings())
        assert identity.org_id == ADMIN_ORG_ID
        assert identity.subject == "admin"

    def test_engineering_demo_api_key_resolves_to_engineering_demo_org(self) -> None:
        identity = resolve_org_identity(authorization="Bearer ai-circus-engineering-2026", settings=FakeSettings())
        assert identity.org_id == ENGINEERING_DEMO_ORG_ID

    def test_never_calls_the_entitlement_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No PlatformRegistryClient.check_entitlement call — that's the whole point."""

        def fail(self: object, **_kwargs: object) -> None:
            raise AssertionError("resolve_org_identity must not call check_entitlement")

        monkeypatch.setattr(auth_module.PlatformRegistryClient, "check_entitlement", fail)
        resolve_org_identity(authorization="Bearer ai-circus-2026", settings=FakeSettings())

    def test_missing_authorization_header_raises_token_validation_error(self) -> None:
        with pytest.raises(TokenValidationError):
            resolve_org_identity(authorization=None, settings=FakeSettings())

    def test_real_token_resolves_to_its_org_claim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            auth_module,
            "validate_token",
            lambda *_a, **_kw: Identity(subject="user-1", org_id="org-1", roles=frozenset()),
        )
        identity = resolve_org_identity(authorization="Bearer good-token", settings=FakeSettings())
        assert identity.org_id == "org-1"

    def test_token_with_no_org_claim_raises_token_validation_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            auth_module,
            "validate_token",
            lambda *_a, **_kw: Identity(subject="user-1", org_id=None, roles=frozenset()),
        )
        with pytest.raises(TokenValidationError):
            resolve_org_identity(authorization="Bearer good-token", settings=FakeSettings())

    def test_keycloak_not_configured_raises_runtime_error(self) -> None:
        with pytest.raises(RuntimeError):
            resolve_org_identity(
                authorization="Bearer anything", settings=FakeSettings(ADMIN_API_KEY=None, KEYCLOAK_ISSUER=None)
            )
