"""Tests for enforce_safe_for_public_deployment — the DEPLOYMENT_TARGET=public boot
gate every service's main() calls before it starts accepting requests."""

from __future__ import annotations

import pytest

from ai_circus_shared.deployment_guard import (
    DEMO_ADMIN_API_KEY,
    DEMO_ENGINEERING_DEMO_API_KEY,
    enforce_safe_for_public_deployment,
)


def _call(
    *,
    admin_api_key: str | None = "rotated-secret",
    engineering_demo_api_key: str | None = None,
    auth_disabled: str = "false",
) -> None:
    enforce_safe_for_public_deployment(
        admin_api_key=admin_api_key,
        engineering_demo_api_key=engineering_demo_api_key,
        auth_disabled=auth_disabled,
    )


def test_noop_when_deployment_target_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEPLOYMENT_TARGET", raising=False)
    _call(admin_api_key=DEMO_ADMIN_API_KEY, auth_disabled="true")  # would fail if enforced


def test_noop_when_deployment_target_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "local")
    _call(admin_api_key=DEMO_ADMIN_API_KEY, engineering_demo_api_key=DEMO_ENGINEERING_DEMO_API_KEY)


def test_passes_when_everything_rotated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "public")
    _call(admin_api_key="a-real-rotated-secret", engineering_demo_api_key=None, auth_disabled="false")


def test_passes_when_admin_key_blanked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "public")
    _call(admin_api_key=None)


def test_rejects_demo_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "public")
    with pytest.raises(RuntimeError, match="ADMIN_API_KEY"):
        _call(admin_api_key=DEMO_ADMIN_API_KEY)


def test_rejects_demo_engineering_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "public")
    with pytest.raises(RuntimeError, match="ENGINEERING_DEMO_API_KEY"):
        _call(engineering_demo_api_key=DEMO_ENGINEERING_DEMO_API_KEY)


def test_rejects_auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "public")
    with pytest.raises(RuntimeError, match="AUTH_DISABLED"):
        _call(auth_disabled="true")


def test_deployment_target_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "PUBLIC")
    with pytest.raises(RuntimeError):
        _call(admin_api_key=DEMO_ADMIN_API_KEY)
