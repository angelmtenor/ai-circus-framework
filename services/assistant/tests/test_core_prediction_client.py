"""Tests for PredictionServiceClient."""

from __future__ import annotations

import httpx
import pytest

from assistant.core import prediction_client as prediction_client_module
from assistant.core.prediction_client import PredictionServiceClient


class _FakeResponse:
    """Minimal stand-in for httpx.Response, covering only what this client reads."""

    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        """Store the fake JSON payload and status code this response should report."""
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """Raise HTTPStatusError for non-2xx status codes, like the real httpx.Response."""
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://prediction:8000/dataset/churn/sample")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self) -> dict[str, object]:
        """Return the fake JSON payload."""
        return self._payload


def test_sample_sends_forwarded_authorization_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """sample() forwards the caller's raw Authorization header and the requested limit."""
    captured: dict[str, object] = {}

    def fake_get(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(payload={"columns": ["a"], "rows": [], "total_rows": 0})

    monkeypatch.setattr(prediction_client_module.httpx, "get", fake_get)
    client = PredictionServiceClient(base_url="http://prediction:8000")

    result = client.sample(scenario_slug="churn", authorization="Bearer tok-1", limit=20)

    assert result == {"columns": ["a"], "rows": [], "total_rows": 0}
    assert captured["url"] == "http://prediction:8000/dataset/churn/sample"
    assert captured["params"] == {"limit": 20}
    assert captured["headers"] == {"Authorization": "Bearer tok-1"}


def test_sample_omits_authorization_header_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller with no Authorization header (e.g. AUTH_DISABLED) sends no header at all."""
    captured: dict[str, object] = {}

    def fake_get(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> _FakeResponse:
        captured["headers"] = headers
        return _FakeResponse(payload={"columns": [], "rows": [], "total_rows": 0})

    monkeypatch.setattr(prediction_client_module.httpx, "get", fake_get)
    client = PredictionServiceClient(base_url="http://prediction:8000")

    client.sample(scenario_slug="churn", authorization=None, limit=20)

    assert captured["headers"] == {}


def test_sample_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-2xx response propagates as an HTTPError — the tool layer decides how to degrade."""
    monkeypatch.setattr(
        prediction_client_module.httpx, "get", lambda *_a, **_kw: _FakeResponse(payload={}, status_code=500)
    )
    client = PredictionServiceClient(base_url="http://prediction:8000")

    with pytest.raises(httpx.HTTPStatusError):
        client.sample(scenario_slug="churn", authorization="Bearer tok-1", limit=20)


def test_evaluation_sends_forwarded_authorization_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """evaluation() hits the evaluation endpoint with the forwarded header and limit."""
    captured: dict[str, object] = {}

    def fake_get(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(payload={"task_type": "regression", "actuals": [1.0], "predictions": [1.1]})

    monkeypatch.setattr(prediction_client_module.httpx, "get", fake_get)
    client = PredictionServiceClient(base_url="http://prediction:8000")

    result = client.evaluation(scenario_slug="motor_speed", authorization="Bearer tok-2", limit=200)

    assert result == {"task_type": "regression", "actuals": [1.0], "predictions": [1.1]}
    assert captured["url"] == "http://prediction:8000/dataset/motor_speed/evaluation"
    assert captured["params"] == {"limit": 200}
    assert captured["headers"] == {"Authorization": "Bearer tok-2"}


def test_evaluation_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-2xx evaluation response propagates as an HTTPError."""
    monkeypatch.setattr(
        prediction_client_module.httpx, "get", lambda *_a, **_kw: _FakeResponse(payload={}, status_code=404)
    )
    client = PredictionServiceClient(base_url="http://prediction:8000")

    with pytest.raises(httpx.HTTPStatusError):
        client.evaluation(scenario_slug="motor_speed", authorization="Bearer tok-2", limit=200)


def test_predict_posts_records_with_forwarded_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    """predict() POSTs the given records and forwards the caller's Authorization header."""
    captured: dict[str, object] = {}

    def fake_post(url: str, *, json: dict[str, object], headers: dict[str, str], timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(payload={"predictions": [{"prediction": 42.0, "contributions": {}}]})

    monkeypatch.setattr(prediction_client_module.httpx, "post", fake_post)
    client = PredictionServiceClient(base_url="http://prediction:8000")

    result = client.predict(scenario_slug="motor_speed", authorization="Bearer tok-3", records=[{"torque": 2.0}])

    assert result == {"predictions": [{"prediction": 42.0, "contributions": {}}]}
    assert captured["url"] == "http://prediction:8000/predict/motor_speed"
    assert captured["json"] == {"records": [{"torque": 2.0}]}
    assert captured["headers"] == {"Authorization": "Bearer tok-3"}


def test_predict_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-2xx predict response propagates as an HTTPError."""
    monkeypatch.setattr(
        prediction_client_module.httpx, "post", lambda *_a, **_kw: _FakeResponse(payload={}, status_code=422)
    )
    client = PredictionServiceClient(base_url="http://prediction:8000")

    with pytest.raises(httpx.HTTPStatusError):
        client.predict(scenario_slug="motor_speed", authorization="Bearer tok-3", records=[{"torque": 2.0}])
