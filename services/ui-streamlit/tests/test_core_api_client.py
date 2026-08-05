"""Tests for the thin HTTP clients calling the backend services."""

from __future__ import annotations

import httpx
import pytest

from ui_streamlit.core.api_client import chat, predict


def _response(url: str, json_body: object) -> httpx.Response:
    """Build an httpx.Response with a request attached (raise_for_status() needs one)."""
    response = httpx.Response(200, json=json_body)
    response.request = httpx.Request("GET", url)
    return response


def test_predict_sends_records_and_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """predict() POSTs the records and attaches the Authorization header when a token is given."""
    captured = {}

    def fake_post(url: str, json: dict, headers: dict, timeout: float) -> httpx.Response:
        captured.update(url=url, json=json, headers=headers)
        return _response(url, {"predictions": [{"probability": 0.5, "contributions": {}}]})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = predict("http://prediction:8000", [{"CreditScore": 600}], access_token="the-token")  # ruff: ignore[hardcoded-password-func-arg]

    assert captured["url"] == "http://prediction:8000/predict"
    assert captured["json"] == {"records": [{"CreditScore": 600}]}
    assert captured["headers"] == {"Authorization": "Bearer the-token"}
    assert result["predictions"][0]["probability"] == pytest.approx(0.5)


def test_predict_omits_authorization_header_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an access token (e.g. DEV_MODE), no Authorization header is sent."""
    captured = {}

    def fake_post(url: str, json: dict, headers: dict, timeout: float) -> httpx.Response:
        captured.update(headers=headers)
        return _response(url, {"predictions": []})

    monkeypatch.setattr(httpx, "post", fake_post)

    predict("http://prediction:8000", [], access_token=None)

    assert captured["headers"] == {}


def test_chat_sends_message_and_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat() POSTs the message and conversation history to /chat."""
    captured = {}

    def fake_post(url: str, json: dict, headers: dict, timeout: float) -> httpx.Response:
        captured.update(url=url, json=json)
        return _response(url, {"reply": "hello back"})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = chat("http://assistant:8000", "hi", [{"role": "user", "content": "earlier"}], access_token="tok")  # ruff: ignore[hardcoded-password-func-arg]

    assert captured["url"] == "http://assistant:8000/chat"
    assert captured["json"] == {"message": "hi", "history": [{"role": "user", "content": "earlier"}]}
    assert result["reply"] == "hello back"
