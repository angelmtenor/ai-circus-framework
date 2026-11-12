"""Tests for crm-mcp's start-up: configuration errors stop it; a valid configuration serves the guarded MCP endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
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


def test_main_serves_the_guarded_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(
        HTTP_PORT="8000",
        LOG_LEVEL="INFO",
        KEYCLOAK_ISSUER="http://idp.test/realms/r",
        KEYCLOAK_JWKS_URL="http://idp.test/realms/r/certs",
        KEYCLOAK_TOKEN_URL="http://idp.test/realms/r/token",  # ruff: ignore[hardcoded-password-func-arg] - a URL, not a password
        MCP_CLIENT_ID="crm-mcp",
        MCP_CLIENT_SECRET=SimpleNamespace(get_secret_value=lambda: "s"),
        API_AUDIENCE="crm-api",
        API_BASE_URL="http://crm-api:8000",
        POLICY_FILE="policy.yaml",
    )
    served: dict[str, Any] = {}
    monkeypatch.setattr(app, "get_env_config", lambda: config)
    monkeypatch.setattr(app.uvicorn, "run", lambda api, **kwargs: served.update(api=api, **kwargs))
    app.main()
    mounted = {getattr(route, "path", "") for route in served["api"].routes}
    assert {"/healthz", "/mcp"} <= mounted and served["port"] == 8000
