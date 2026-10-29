"""The data_product chat tools: forwarded identity, view-as headers, errors as data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from ai_circus_shared.scenario_schema import ScenarioDefinition

from assistant.core import data_tools
from assistant.core.data_tools import DataApiClient, build_data_tools, data_system_prompt

RETAIL = Path(__file__).resolve().parents[3] / "scenarios" / "surf_retail" / "scenario.yaml"


@pytest.fixture
def definition() -> ScenarioDefinition:
    return ScenarioDefinition.load(RETAIL)


def test_client_forwards_authorization_and_view_as(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_get(url: str, params: Any = None, headers: Any = None, timeout: float = 0) -> httpx.Response:
        seen.update(url=url, params=params, headers=headers)
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", url))

    monkeypatch.setattr(data_tools.httpx, "get", fake_get)
    client = DataApiClient(
        "http://data-api:8000/", "surf_retail", "Bearer t", {"role": "retail_store_manager", "values": ["S012"]}
    )
    assert client.get("/metrics") == {"ok": True}
    assert seen["url"] == "http://data-api:8000/v1/surf_retail/metrics"
    assert seen["headers"] == {
        "Authorization": "Bearer t",
        "X-Data-View-As": "retail_store_manager",
        "X-Data-View-As-Values": "S012",
    }


def test_errors_come_back_as_data(monkeypatch: pytest.MonkeyPatch, definition: ScenarioDefinition) -> None:
    def fake_get(url: str, params: Any = None, headers: Any = None, timeout: float = 0) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Role 'x' has no access"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(data_tools.httpx, "get", fake_get)
    tools = {t.name: t for t in build_data_tools(DataApiClient("http://d", "surf_retail", None, None), definition)}
    assert set(tools) == {
        "list_metrics",
        "query_metric",
        "get_record",
        "search_records",
        "data_health",
        "run_sql",
        "find_products",  # the data product declares a storefront…
        "bought_together",  # …with a recommendations dataset; no ML tools without a prediction client
    }
    result = json.loads(
        tools["query_metric"].invoke({"metric": "revenue", "dims": ["store_id"], "filters": {"channel": ["store"]}})
    )
    assert result == {"error": "Role 'x' has no access", "status": 403}


def test_metric_params_and_sql(monkeypatch: pytest.MonkeyPatch, definition: ScenarioDefinition) -> None:
    calls: list[Any] = []

    def fake_get(url: str, params: Any = None, headers: Any = None, timeout: float = 0) -> httpx.Response:
        calls.append(params)
        return httpx.Response(200, json={"rows": []}, request=httpx.Request("GET", url))

    def fake_post(url: str, json: Any = None, headers: Any = None, timeout: float = 0) -> httpx.Response:
        return httpx.Response(200, json={"rows": [{"n": i} for i in range(100)]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(data_tools.httpx, "get", fake_get)
    monkeypatch.setattr(data_tools.httpx, "post", fake_post)
    tools = {t.name: t for t in build_data_tools(DataApiClient("http://d", "surf_retail", None, None), definition)}
    tools["query_metric"].invoke({
        "metric": "revenue",
        "dims": ["store_id", "channel"],
        "filters": {"channel": ["store", "online"]},
    })
    assert calls[0]["dims"] == "store_id,channel" and calls[0]["filters"] == "channel:store,online"
    assert len(json.loads(tools["run_sql"].invoke({"sql": "SELECT 1"}))["rows"]) == 60


def test_system_prompt_lists_the_metrics(definition: ScenarioDefinition) -> None:
    prompt = data_system_prompt(definition)
    assert "never from memory" in prompt and "- revenue (currency_cents)" in prompt


def test_catalogue_recommendation_and_ml_tools(monkeypatch: pytest.MonkeyPatch, definition: ScenarioDefinition) -> None:
    """find_products searches the catalogue; ml_predict scores the 'now' rows of a feature mart."""
    children = {
        slug: ScenarioDefinition.load(RETAIL.parents[1] / slug / "scenario.yaml")
        for slug in ("surf_demand_forecast", "surf_customer_propensity")
    }
    calls: list[tuple[str, Any]] = []

    def fake_get(url: str, params: Any = None, headers: Any = None, timeout: float = 0) -> httpx.Response:
        calls.append((url, params))
        if "mart_catalogue" in url:
            rows = [
                {"sku": "FSH-0102", "product_name": "Bonito 5'6\"", "category": "Fish", "brand": "Levante Shapes"},
                {"sku": "LSH-0201", "product_name": "Regular Leash 7 ft", "category": "Leashes", "brand": "Garbí Gear"},
            ]
        else:  # ml_demand_features: one past week (target known) and the week to forecast
            base = {"store_id": "S001", "rider_group": "sup", "week": "2026-10-19"}
            rows = [{**base, "units_next_week": 12}, {**base, "week": "2026-10-26", "units_next_week": None}]
        return httpx.Response(200, json={"rows": rows}, request=httpx.Request("GET", url))

    class FakePrediction:
        def predict(self, *, scenario_slug: str, authorization: str | None, records: list[dict[str, Any]]) -> Any:
            assert scenario_slug == "surf_demand_forecast" and authorization == "Bearer t"
            return {"predictions": [{"prediction": 9.5, "contributions": {f"f{i}": i for i in range(10)}}]}

    monkeypatch.setattr(data_tools.httpx, "get", fake_get)
    tools = {
        t.name: t
        for t in build_data_tools(
            DataApiClient("http://d", "surf_retail", "Bearer t", None),
            definition,
            definitions=children,
            prediction=FakePrediction(),
            authorization="Bearer t",
        )
    }
    found = json.loads(tools["find_products"].invoke({"query": "bonito"}))
    assert found["matches"] == 1 and found["products"][0]["sku"] == "FSH-0102"
    tools["bought_together"].invoke({"product_id": "FSH-0102"})
    assert calls[-1][1]["where"] == "sku=FSH-0102"
    result = json.loads(
        tools["ml_predict"].invoke({"model": "surf_demand_forecast", "where": {"store_id": "S001"}, "limit": 1})
    )
    assert calls[-1][1]["where"] == ["store_id=S001"]
    first = result["results"][0]
    assert first["actual"] is None and first["week"] == "2026-10-26"  # the week still to come comes first
    assert len(first["prediction"]["contributions"]) == 6
    assert json.loads(tools["ml_predict"].invoke({"model": "nope"}))["error"].startswith("Unknown model")
