"""Tests for crm-mcp's start-up: configuration errors stop it; a valid configuration serves the guarded MCP endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp_kit.testing import write_test_pki
from pydantic import BaseModel, ValidationError

import crm_mcp.app as app


def build_validation_error() -> ValidationError:
    """A real Pydantic validation error."""

    class RequiredConfig(BaseModel):
        required_value: int

    try:
        RequiredConfig()  # ty: ignore[missing-argument]
    except ValidationError as exc:
        return exc
    raise AssertionError("Expected ValidationError was not raised")


def test_main_exits_on_invalid_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> Any:
        raise build_validation_error()

    monkeypatch.setattr(app, "get_env_config", broken)
    with pytest.raises(SystemExit):
        app.main()


def level3_config(tmp_path: Any, *, with_tls: bool = True) -> SimpleNamespace:
    """The settings `main()` reads, with the generated policy.yaml (level 3) and, unless told not to, TLS files."""
    tls: dict[str, str | None] = {"API_TLS_CA_FILE": None, "API_TLS_CERT_FILE": None, "API_TLS_KEY_FILE": None}
    if with_tls:
        files = write_test_pki(tmp_path)
        tls = {
            "API_TLS_CA_FILE": str(files["ca"]),
            "API_TLS_CERT_FILE": str(files["client_cert"]),
            "API_TLS_KEY_FILE": str(files["client_key"]),
        }
    return SimpleNamespace(
        HTTP_PORT="8000",
        LOG_LEVEL="INFO",
        KEYCLOAK_ISSUER="http://idp.test/realms/r",
        KEYCLOAK_JWKS_URL="http://idp.test/realms/r/certs",
        KEYCLOAK_TOKEN_URL="http://idp.test/realms/r/token",  # ruff: ignore[hardcoded-password-func-arg] - a URL, not a password
        KEYCLOAK_INTROSPECTION_URL=None,
        MCP_CLIENT_ID="crm-mcp",
        MCP_CLIENT_SECRET=SimpleNamespace(get_secret_value=lambda: "s"),
        API_AUDIENCE="crm-api",
        API_BASE_URL="https://crm-api:8443",
        POLICY_FILE="policy.yaml",
        **tls,
    )


def test_main_serves_the_guarded_endpoint_at_level_3(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    served: dict[str, Any] = {}
    monkeypatch.setattr(app, "get_env_config", lambda: level3_config(tmp_path))
    monkeypatch.setattr(app.uvicorn, "run", lambda api, **kwargs: served.update(api=api, **kwargs))
    app.main()
    mounted = {getattr(route, "path", "") for route in served["api"].routes}
    assert {"/healthz", "/mcp"} <= mounted and served["port"] == 8000


def test_level_3_without_its_tls_files_refuses_to_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(app, "get_env_config", lambda: level3_config(tmp_path, with_tls=False))
    monkeypatch.setattr(app.uvicorn, "run", lambda *_a, **_k: pytest.fail("must not serve below its level"))
    with pytest.raises(SystemExit) as exited:
        app.main()
    assert exited.value.code == 1


def test_the_policy_names_level_3_and_the_kit_gets_every_part(tmp_path: Any) -> None:
    kit = app.build_kit(level3_config(tmp_path))  # ty: ignore[invalid-argument-type]
    assert kit.level == 3 and kit.introspector is not None and kit.api_tls is not None
    assert kit.introspector.settings.url == "http://idp.test/realms/r/token/introspect"
    assert kit.exchanger is not None and kit.exchanger.settings.dpop_key is not None
