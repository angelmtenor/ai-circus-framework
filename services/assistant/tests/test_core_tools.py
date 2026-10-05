"""Tests for the chat tools: prediction-service-backed ones and a scenario's document search."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from ai_circus_shared.scenario_schema import ScenarioDefinition

from assistant.core.tools import build_document_tool, build_prediction_tools
from tests.test_core_chat import DEFINITION_WITH_DOCUMENTS


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
        self.calls.append((
            "evaluation",
            {"scenario_slug": scenario_slug, "authorization": authorization, "limit": limit},
        ))
        if self.raise_error:
            raise self.raise_error
        return {"actuals": [1.0], "predictions": [1.1], "metrics": {"r2": 0.99}}

    def predict(
        self, *, scenario_slug: str, authorization: str | None, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Record the call and either raise the configured error or return a fixed payload."""
        self.calls.append((
            "predict",
            {"scenario_slug": scenario_slug, "authorization": authorization, "records": records},
        ))
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
    assert client.calls == [("sample", {"scenario_slug": "motor_speed", "authorization": "Bearer tok-1", "limit": 10})]


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


def test_long_text_values_are_clipped_in_sample_and_evaluation_output() -> None:
    """A 1,000-character review must not be dumped whole into the LLM's context."""
    review = "Management " * 100
    client = _FakeClient()
    client.sample = lambda **_: {"rows": [{"Review": review, "pay": 3}]}  # type: ignore[method-assign]
    client.evaluation = lambda **_: {"feature_values": {"Review": [review]}}  # type: ignore[method-assign]
    tools = build_prediction_tools(client, scenario_slug="toxic_leadership", authorization=None)

    row = json.loads(_tool_named(tools, "get_dataset_sample").func(limit=1))["rows"][0]
    assert len(row["Review"]) == 281 and row["Review"].endswith("…") and row["pay"] == 3
    values = json.loads(_tool_named(tools, "get_predictions_vs_actuals").func(limit=1))["feature_values"]["Review"]
    assert len(values[0]) == 121


class _FakeEmbedder:
    def encode_query(self, text: str) -> list[float]:
        return [0.5, 0.5]


class _FakeQdrant:
    """In-memory stand-in for QdrantClient: one collection, fixed points, a call log."""

    def __init__(self, points: list[SimpleNamespace], *, exists: bool = True) -> None:
        self.points, self.exists, self.queried = points, exists, []

    def collection_exists(self, collection_name: str, /) -> bool:
        return self.exists

    def query_points(self, collection_name: str, *, query: list[float], limit: int) -> SimpleNamespace:
        self.queried.append((collection_name, limit))
        return SimpleNamespace(points=self.points)


def _point(text: str, source: str) -> SimpleNamespace:
    return SimpleNamespace(payload={"text": text, "source": source}, score=0.8)


def _document_tool(qdrant: _FakeQdrant, definition: ScenarioDefinition = DEFINITION_WITH_DOCUMENTS) -> Any:
    return build_document_tool(qdrant, _FakeEmbedder(), definition, org_id="org-1")


def test_document_tool_takes_its_name_and_description_from_the_scenario() -> None:
    tool = _document_tool(_FakeQdrant([]))

    assert tool.name == "consultar_normativa"
    assert tool.description.startswith("Busca en la normativa")


def test_document_tool_searches_the_callers_tenant_collection_and_tags_sources() -> None:
    qdrant = _FakeQdrant([_point("Artículo 4. Requisitos.", "normativa/bases_art04.md")])

    content = _document_tool(qdrant).invoke({"query": "requisitos de la ayuda"})

    assert qdrant.queried == [("normativa_demo__org-1", 4)]
    assert '<retrieved_document source="normativa/bases_art04.md">' in content
    assert "Artículo 4. Requisitos." in content


def test_document_tool_says_so_when_nothing_is_indexed() -> None:
    content = _document_tool(_FakeQdrant([], exists=False)).invoke({"query": "plazo de subsanación"})

    assert content == "No relevant documents were found for this query."


def test_document_tool_stops_searching_after_its_per_run_cap() -> None:
    qdrant = _FakeQdrant([_point("Art. 68.", "normativa/ley39_art68.md")])
    tool = _document_tool(qdrant)

    results = [tool.invoke({"query": f"pregunta {i}"}) for i in range(3)]

    assert len(qdrant.queried) == 2  # max_calls_per_run in DEFINITION_WITH_DOCUMENTS
    assert results[2].startswith("Search limit reached")


def test_document_tool_rejects_an_oversized_query() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _document_tool(_FakeQdrant([])).invoke({"query": "x" * 501})


def test_document_tool_reports_an_unreachable_index_instead_of_failing_the_answer() -> None:
    class _DownQdrant(_FakeQdrant):
        def collection_exists(self, collection_name: str, /) -> bool:
            raise httpx.ConnectError("Connection refused")

    content = _document_tool(_DownQdrant([])).invoke({"query": "requisitos"})

    assert content.startswith("Could not search the documents")
