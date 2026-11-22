"""Tests for api/ws.py's WebSocket auth/routing handshake — the part that runs
before any Pipecat pipeline is built. The pipeline itself (push-to-talk/STT/TTS/AgentBridge)
is exercised by test_push_to_talk.py, test_agent_bridge.py, test_providers.py, and the manual
docker-compose smoke test in the plan's verification section, not here.
"""

from __future__ import annotations

from collections.abc import Generator
from urllib.parse import urlencode

import pytest
from ai_circus_shared.auth import Identity, TokenValidationError
from ai_circus_shared.entitlements import EntitlementDeniedError, ScenarioSummary
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipecat.frames.frames import InterruptionFrame, OutputTransportMessageFrame
from starlette.websockets import WebSocketDisconnect

import agui_voice.api.ws as ws_module
from agui_voice.api.ws import BrowserFrameSerializer, router


class FakeConfig:
    """Minimal stand-in for EnvConfig, covering the fields voice_ws reads."""

    PLATFORM_REGISTRY_URL = "http://platform-registry:8000"
    ASSISTANT_SERVICE_URL = "http://assistant:8000"
    RAG_AGENT_SERVICE_URL = "http://rag-agent:8000"
    FORM_AGENT_SERVICE_URL = "http://form-agent:8000"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    monkeypatch.setattr(ws_module, "get_env_config", lambda: FakeConfig())
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)


def test_ws_closes_4401_on_invalid_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad/missing token closes with the 4401 policy-violation code, before any
    pipeline or entitlement lookup happens.
    """

    def raise_invalid(_scenario_slug: str, _authorization: str | None) -> Identity:
        raise TokenValidationError("bad token")

    monkeypatch.setattr(ws_module, "resolve_identity_from_token", raise_invalid)

    with client.websocket_connect("/ws/churn") as ws, pytest.raises(WebSocketDisconnect) as exc_info:
        ws.receive_text()

    assert exc_info.value.code == 4401


def test_ws_closes_4403_on_entitlement_denied(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid but unentitled caller closes with 4403, before any pipeline is built."""

    def raise_denied(_scenario_slug: str, _authorization: str | None) -> Identity:
        raise EntitlementDeniedError("not entitled")

    monkeypatch.setattr(ws_module, "resolve_identity_from_token", raise_denied)

    with client.websocket_connect("/ws/churn?token=good") as ws, pytest.raises(WebSocketDisconnect) as exc_info:
        ws.receive_text()

    assert exc_info.value.code == 4403


def test_ws_closes_4404_when_scenario_not_servable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """An entitled caller asking for a scenario_slug/kind this deployment can't route
    to any of assistant/rag-agent/form-agent closes with 4404.
    """
    monkeypatch.setattr(
        ws_module,
        "resolve_identity_from_token",
        lambda _slug, _auth: Identity(subject="user-1", org_id="org-1", roles=frozenset()),
    )

    class FakeRegistryClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def list_scenarios(self, *, org_id: str, authorization: str | None = None) -> list[ScenarioSummary]:
            return [
                ScenarioSummary(
                    slug="other-scenario",
                    kind="tabular_ml",
                    title="Other",
                    description="",
                    icon="x",
                    industry="general",
                )
            ]

    monkeypatch.setattr(ws_module, "PlatformRegistryClient", FakeRegistryClient)

    with client.websocket_connect("/ws/churn?token=good") as ws, pytest.raises(WebSocketDisconnect) as exc_info:
        ws.receive_text()

    assert exc_info.value.code == 4404


@pytest.mark.parametrize("thread_id", ["", "../conversations", "a b", "x" * 129])
def test_ws_closes_4400_on_a_malformed_thread_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, thread_id: str
) -> None:
    """The conversation id is checked before anything else, so a malformed one never reaches the agent's URL."""

    def must_not_run(_scenario_slug: str, _authorization: str | None) -> Identity:
        raise AssertionError("a malformed thread id must be refused before authentication")

    monkeypatch.setattr(ws_module, "resolve_identity_from_token", must_not_run)

    url = f"/ws/churn?token=good&{urlencode({'thread_id': thread_id})}"
    with client.websocket_connect(url) as ws, pytest.raises(WebSocketDisconnect) as exc_info:
        ws.receive_text()

    assert exc_info.value.code == 4400


async def test_browser_serializer_drops_the_interruption_frame_only() -> None:
    """The browser cannot read an interruption frame (it hears of it as `bot-interrupted`); messages still go out."""
    serializer = BrowserFrameSerializer()

    assert await serializer.serialize(InterruptionFrame()) is None
    assert await serializer.serialize(OutputTransportMessageFrame(message={"type": "bot-ready"}))
