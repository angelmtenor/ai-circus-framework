"""Tests for crm-api's start-up: configuration errors stop it; a valid configuration serves the API."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

import crm_api.app as app


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


def level_config(level: str, **overrides: Any) -> SimpleNamespace:
    """The settings `main()` reads, at one security level (level 3 complete unless overridden)."""
    values: dict[str, Any] = {
        "HTTP_PORT": "8000",
        "LOG_LEVEL": "INFO",
        "KEYCLOAK_ISSUER": "http://idp.test/realms/r",
        "KEYCLOAK_JWKS_URL": "http://idp.test/realms/r/certs",
        "API_AUDIENCE": "crm-api",
        "SECURITY_LEVEL": level,
        "KEYCLOAK_INTROSPECTION_URL": "http://idp.test/realms/r/introspect",
        "API_CLIENT_SECRET": SecretStr("s"),
        "TLS_PORT": "8443",
        "TLS_CERT_FILE": "/tls/tls.crt",
        "TLS_KEY_FILE": "/tls/tls.key",
        "TLS_CLIENT_CA_FILE": "/tls/ca.crt",
    }
    return SimpleNamespace(**{**values, **overrides})


def test_main_at_level_3_serves_mutual_tls_and_plain_health(monkeypatch: pytest.MonkeyPatch) -> None:
    served: dict[str, Any] = {}

    async def fake_serve(servers: list[Any]) -> None:
        await asyncio.sleep(0)
        served["ports"] = [s.config.port for s in servers]
        served["client_ca"] = [s.config.ssl_ca_certs for s in servers]

    monkeypatch.setattr(app, "get_env_config", lambda: level_config("3"))
    monkeypatch.setattr(app, "serve_together", fake_serve)
    app.main()
    assert served == {"ports": [8000, 8443], "client_ca": [None, "/tls/ca.crt"]}


def test_main_refuses_level_3_without_its_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "get_env_config", lambda: level_config("3", TLS_CLIENT_CA_FILE=None))
    with pytest.raises(SystemExit) as exited:
        app.main()
    assert exited.value.code == 1


def test_main_serves_the_api_with_the_configured_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    config = level_config("2")
    served: dict[str, Any] = {}
    monkeypatch.setattr(app, "get_env_config", lambda: config)
    monkeypatch.setattr(app.uvicorn, "run", lambda api, **kwargs: served.update(api=api, **kwargs))
    app.main()
    settings = served["api"].state.token_settings
    assert (settings.issuer, settings.audience, served["port"]) == ("http://idp.test/realms/r", "crm-api", 8000)
