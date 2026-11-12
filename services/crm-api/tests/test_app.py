"""Tests for crm-api's start-up: configuration errors stop it; a valid configuration serves the API."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

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


def test_main_serves_the_api_with_the_configured_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(
        HTTP_PORT="8000",
        LOG_LEVEL="INFO",
        KEYCLOAK_ISSUER="http://idp.test/realms/r",
        KEYCLOAK_JWKS_URL="http://idp.test/realms/r/certs",
        API_AUDIENCE="crm-api",
    )
    served: dict[str, Any] = {}
    monkeypatch.setattr(app, "get_env_config", lambda: config)
    monkeypatch.setattr(app.uvicorn, "run", lambda api, **kwargs: served.update(api=api, **kwargs))
    app.main()
    settings = served["api"].state.token_settings
    assert (settings.issuer, settings.audience, served["port"]) == ("http://idp.test/realms/r", "crm-api", 8000)
