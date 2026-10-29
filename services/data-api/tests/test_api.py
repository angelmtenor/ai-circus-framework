"""Route-level tests: access resolution is overridden, the warehouse is a fake."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from data_api.api import router
from data_api.core.events_relay import EventsRelay
from data_api.core.identity import get_access, get_product
from tests.conftest_products import access_as, retail
from tests.fakes import FakeWarehouse


class FakeSimulation:
    """Records simulator calls instead of producing anything."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[tuple[Any, ...]] = []

    def status(self, product: Any, org: str) -> dict[str, Any]:
        """A fixed idle status."""
        return {"available": True, "live": False}

    def act(self, product: Any, org: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Record the action; 'nope' is unknown."""
        self.calls.append(("act", org, action, params))
        if action == "nope":
            raise LookupError("Unknown simulator action 'nope'.")
        return {"action": action, "record_id": "S012-R01-000000001"}

    def start_backfill(self, product: Any, org: str, days: int | None) -> dict[str, Any]:
        """Record the backfill request."""
        self.calls.append(("backfill", org, days))
        return {"state": "running"}

    def set_live(self, product: Any, org: str, on: bool, minutes: int) -> dict[str, Any]:
        """Record the live toggle."""
        self.calls.append(("live", org, on, minutes))
        return {"live": on}


def handler(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    if 'FROM "surf_retail"."hot_sales_minute"' in sql and "max(updated_at)" in sql:
        return [{"receipts": 3, "revenue_cents": 300, "units": 5, "updated_at": None}]
    if "count(*) FILTER" in sql:
        return [{"open": 1, "today": 1}]
    if "count(*) AS n" in sql:
        return [{"n": 10, "latest": "2026-10-28"}]
    if "GROUP BY" in sql and "AS value" in sql:
        return [{"store_id": "S012", "value": 100}]
    if "AS value" in sql:
        return [{"value": 100}]
    return []


@pytest.fixture
def make_client() -> Iterator[Any]:
    from fastapi import FastAPI

    def build(role: str | None = None, values: tuple[str, ...] = ()) -> tuple[TestClient, FakeSimulation]:
        app = FastAPI()
        app.include_router(router)
        simulation = FakeSimulation()
        app.state.runtime = SimpleNamespace(
            products={"surf_retail": retail()},
            warehouse=FakeWarehouse(handler),
            simulation=simulation,
            relay=EventsRelay("localhost:1"),
            tiers=lambda: {"core": True},
        )
        app.dependency_overrides[get_product] = retail
        app.dependency_overrides[get_access] = lambda: access_as(role, values=values)
        return TestClient(app), simulation

    yield build


def test_metric_route(make_client: Any) -> None:
    client, _ = make_client()
    body = client.get("/v1/surf_retail/metrics/revenue", params={"dims": "store_id", "period": "today"}).json()
    assert body["metric"] == "revenue" and body["rows"] == [{"store_id": "S012", "value": 100}]
    assert client.get("/v1/surf_retail/metrics/revenue", params={"dims": "sku"}).status_code == 422
    assert client.get("/v1/surf_retail/metrics/nope").status_code == 422


def test_metric_catalogue_and_datasets(make_client: Any) -> None:
    client, _ = make_client("retail_analyst")
    assert any(m["name"] == "revenue" for m in client.get("/v1/surf_retail/metrics").json())
    datasets = client.get("/v1/surf_retail/datasets").json()
    assert {d["layer"] for d in datasets} == {"silver", "gold", "ops"}


def test_overview_hides_ops_and_simulator_from_a_store_manager(make_client: Any) -> None:
    client, _ = make_client("retail_store_manager", ("S012",))
    body = client.get("/v1/surf_retail/overview").json()
    assert body["access"]["role"] == "retail_store_manager"
    assert body["access"]["row_filter"] == {"column": "store_id", "values": ["S012"]}
    assert body["ops"] == {} and body["simulator"] is None


def test_simulator_routes_need_write_access(make_client: Any) -> None:
    client, simulation = make_client()
    ok = client.post("/v1/surf_retail/simulator/actions/sell", json={"params": {"store_id": "S012"}})
    assert ok.status_code == 200 and simulation.calls[0][:3] == ("act", "acme", "sell")
    assert client.post("/v1/surf_retail/simulator/actions/nope", json={}).status_code == 404
    assert client.post("/v1/surf_retail/simulator/backfill", json={"days": 3}).status_code == 202
    assert client.post("/v1/surf_retail/simulator/live", json={"on": True, "minutes": 5}).json() == {"live": True}
    viewing, _ = make_client("retail_analyst")  # admin "viewing as" never writes
    assert viewing.post("/v1/surf_retail/simulator/actions/sell", json={}).status_code == 403


def test_query_route_refuses_unsafe_sql(make_client: Any) -> None:
    client, _ = make_client("retail_analyst")
    assert client.post("/v1/surf_retail/query", json={"sql": "DROP TABLE mart_sales_daily"}).status_code == 422
    manager, _ = make_client("retail_store_manager", ("S012",))
    assert manager.post("/v1/surf_retail/query", json={"sql": "SELECT 1"}).status_code == 403


def test_hot_route_and_events(make_client: Any) -> None:
    client, _ = make_client()
    body = client.get("/v1/surf_retail/hot/sales_minute").json()
    assert body["approximate"] is True and body["today"]["receipts"] == 3
    assert client.get("/v1/surf_retail/hot/nope").status_code == 404
    assert client.get("/v1/surf_retail/events").json() == []


def test_visible_events_filter_by_row_value() -> None:
    from data_api.api import _visible_events

    events = [{"filter": "S012", "record_id": "a"}, {"filter": "S001", "record_id": "b"}, {"filter": None}]
    assert _visible_events(access_as("retail_store_manager", values=("S012",)), events) == [events[0]]
    assert _visible_events(access_as(), events) == events
