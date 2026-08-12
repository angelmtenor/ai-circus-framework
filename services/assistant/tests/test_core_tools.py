"""Tests for the prediction-service-backed chat tools."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from assistant.core.tools import build_prediction_tools


class _FakeClient:
    """Stand-in for PredictionServiceClient, capturing call args and returning/raising on demand."""

    def __init__(self) -> None:
        """Start with no configured failure and an empty call log."""
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.raise_error: httpx.HTTPError | None = None

    def sample(self, *, scenario_slug: str, authorization: str | None, limit: int) -> dict[str, Any]:
        """Record the call and either raise the configured error or return a fixed payload."""
        self.calls.append(("sample", {"scenario_slug": scenario_slug, "authorization": authorization, "limit": limit}))
        if self.raise_error:
            raise self.raise_error
        return {"columns": ["torque"], "rows": [{"torque": 1.0}], "total_rows": 1}

    def evaluation(self, *, scenario_slug: str, authorization: str | None, limit: int) -> dict[str, Any]:
        """Record the call and either raise the configured error or return a fixed payload."""
        self.calls.append(
            ("evaluation", {"scenario_slug": scenario_slug, "authorization": authorization, "limit": limit})
        )
        if self.raise_error:
            raise self.raise_error
        return {"actuals": [1.0], "predictions": [1.1], "metrics": {"r2": 0.99}}

    def predict(self, *, scenario_slug: str, authorization: str | None, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Record the call and either raise the configured error or return a fixed payload."""
        self.calls.append(
            ("predict", {"scenario_slug": scenario_slug, "authorization": authorization, "records": records})
        )
        if self.raise_error:
            raise self.raise_error
        return {"predictions": [{"prediction": 42.0, "contributions": {"torque": 0.5}}]}


def _tool_named(tools: list[Any], name: str) -> Any:
    return next(tool for tool in tools if tool.name == name)


def test_build_prediction_tools_returns_the_three_expected_tools() -> None:
    """The tool set exposes exactly get_dataset_sample, get_predictions_vs_actuals, predict_records."""
    tools = build_prediction_tools(_FakeClient(), scenario_slug="churn", authorization="Bearer tok")

    assert {tool.name for tool in tools} == {"get_dataset_sample", "get_predictions_vs_actuals", "predict_records"}


def test_get_dataset_sample_is_scoped_to_the_calling_scenario_and_auth() -> None:
    """get_dataset_sample closes over this request's scenario_slug and forwarded auth header."""
    client = _FakeClient()
    tools = build_prediction_tools(client, scenario_slug="motor_speed", authorization="Bearer tok-1")

    result = _tool_named(tools, "get_dataset_sample").func(limit=10)

    assert json.loads(result) == {"columns": ["torque"], "rows": [{"torque": 1.0}], "total_rows": 1}
    assert client.calls == [
        ("sample", {"scenario_slug": "motor_speed", "authorization": "Bearer tok-1", "limit": 10})
    ]


def test_get_predictions_vs_actuals_returns_real_evaluation_data() -> None:
    """get_predictions_vs_actuals returns the client's actuals/predictions/metrics as JSON."""
    client = _FakeClient()
    tools = build_prediction_tools(client, scenario_slug="motor_speed", authorization="Bearer tok-1")

    result = _tool_named(tools, "get_predictions_vs_actuals").func(limit=50)

    assert json.loads(result) == {"actuals": [1.0], "predictions": [1.1], "metrics": {"r2": 0.99}}


def test_predict_records_forwards_records_and_returns_predictions() -> None:
    """predict_records passes the model's records through and returns real predictions/contributions."""
    client = _FakeClient()
    tools = build_prediction_tools(client, scenario_slug="motor_speed", authorization="Bearer tok-1")

    result = _tool_named(tools, "predict_records").func(records=[{"torque": 2.0}])

    assert json.loads(result) == {"predictions": [{"prediction": 42.0, "contributions": {"torque": 0.5}}]}
    assert client.calls == [
        (
            "predict",
            {"scenario_slug": "motor_speed", "authorization": "Bearer tok-1", "records": [{"torque": 2.0}]},
        )
    ]


@pytest.mark.parametrize(
    ("tool_name", "kwargs"),
    [
        ("get_dataset_sample", {"limit": 10}),
        ("get_predictions_vs_actuals", {"limit": 50}),
        ("predict_records", {"records": [{"torque": 2.0}]}),
    ],
)
def test_each_tool_degrades_to_a_plain_string_on_http_error(tool_name: str, kwargs: dict[str, Any]) -> None:
    """A prediction-service outage returns a plain-string error, not an unhandled exception —
    so the chat turn can still answer gracefully instead of erroring out.
    """
    client = _FakeClient()
    client.raise_error = httpx.ConnectError("connection refused")
    tools = build_prediction_tools(client, scenario_slug="motor_speed", authorization="Bearer tok-1")

    result = _tool_named(tools, tool_name).func(**kwargs)

    assert isinstance(result, str)
    assert "prediction service is unavailable" in result
