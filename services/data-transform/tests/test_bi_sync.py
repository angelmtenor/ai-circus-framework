"""Dashboards as code: chart params, the layout grid and a full sync against a fake Superset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from data_transform.bi_sync import MARTS_DB, WAREHOUSE_DB, chart_params, ensure_databases, layout, sync_product
from data_transform.core.products import DataProduct, load_products

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


@pytest.fixture(scope="module")
def product() -> DataProduct:
    return load_products(SCENARIOS, "surf_retail")["surf_retail"]


class FakeSuperset:
    """An in-memory Superset API: every resource is a list of dicts with ids."""

    def __init__(self) -> None:
        """Start with the built-in Gamma role and no objects."""
        self.store: dict[str, list[dict[str, Any]]] = {"security/roles": [{"id": 1, "name": "Gamma"}]}
        self.calls: list[tuple[str, str, Any]] = []

    def _items(self, resource: str) -> list[dict[str, Any]]:
        return self.store.setdefault(resource, [])

    def call(self, method: str, path: str, body: Any = None, **params: Any) -> Any:
        """Serve the handful of endpoints bi-sync uses."""
        self.calls.append((method, path, body))
        parts = path.split("?")[0].strip("/").split("/")[2:]  # after api/v1, without the query string
        resource = "security/" + parts[1] if parts[0] == "security" else parts[0]
        tail = parts[2:] if parts[0] == "security" else parts[1:]
        items = self._items(resource)
        if method == "POST":
            item = {"id": len(items) + 1, **(body or {})}
            if resource == "dataset":
                item.update(
                    metrics=[{"id": 99, "metric_name": "count", "expression": "COUNT(*)"}],
                    database={"id": body["database"]},
                )
            items.append(item)
            return {"id": item["id"]}
        if method == "GET" and tail and tail[0].isdigit():
            return {"result": next(i for i in items if i["id"] == int(tail[0]))}
        if method == "GET":
            return {"result": items}
        if method == "PUT" and tail and tail[0].isdigit():
            if len(tail) == 1:
                item = next(i for i in items if i["id"] == int(tail[0]))
                if resource == "dataset" and body and "metrics" in body:
                    names = [m["metric_name"] for m in body["metrics"]]
                    known = {m["metric_name"]: m["id"] for m in item.get("metrics", [])}
                    clash = [
                        m
                        for m in body["metrics"]
                        if m["metric_name"] in known and m.get("id") != known[m["metric_name"]]
                    ]
                    assert not clash and len(names) == len(set(names)), "Superset: one or more metrics already exist"
                    body = {
                        **body,
                        "metrics": [{**m, "id": m.get("id", 100 + n)} for n, m in enumerate(body["metrics"])],
                    }
                item.update(body or {})
            return {}
        return {}

    def find(self, resource: str, column: str, value: Any) -> dict[str, Any] | None:
        """Like the real client's filter-by-column."""
        return next((i for i in self._items(resource) if i.get(column) == value), None)

    def upsert(self, resource: str, column: str, value: Any, body: dict[str, Any]) -> int:
        """Like the real client's create-or-update."""
        found = self.find(resource, column, value)
        if found is None:
            return int(self.call("POST", f"/api/v1/{resource}/", body)["id"])
        found.update(body)
        return int(found["id"])


def test_chart_params_per_viz() -> None:
    big = chart_params({"viz": "big_number", "metric": "revenue"}, "day")
    assert big["viz_type"] == "big_number_total" and big["adhoc_filters"][0]["comparator"] == "Last month"
    bar = chart_params({"viz": "bar", "metric": "revenue", "groupby": ["store_name"]}, "day")
    assert (bar["x_axis"], bar["x_axis_sort"], bar["xAxisForceCategorical"]) == ("store_name", "revenue", True)
    live = chart_params(
        {"viz": "line", "metric": "m", "time": "minute", "groupby": ["channel"], "last_minutes": 120}, None
    )
    assert live["time_grain_sqla"] == "PT1M" and live["adhoc_filters"][0]["comparator"] == "Last 120 minutes : now"
    today = chart_params({"viz": "big_number", "metric": "m", "today": True}, "minute")
    assert today["adhoc_filters"][0]["comparator"] == "Today : Tomorrow"


def test_rison_encoding() -> None:
    from data_transform.bi_sync import rison

    assert rison({"filters": [{"col": "slice_name", "opr": "eq", "value": "Revenue (€)"}], "page_size": 100}) == (
        "(filters:!((col:slice_name,opr:eq,value:'Revenue (€)')),page_size:100)"
    )
    assert rison("it's!") == "'it!'s!!'" and rison(True) == "!t" and rison(None) == "!n" and rison("2026") == "'2026'"


def test_layout_wraps_rows_at_twelve_columns() -> None:
    position = layout("T", [(1, "a", 6, 30), (2, "b", 6, 30), (3, "c", 8, 60)])
    assert position["GRID_ID"]["children"] == ["ROW-1", "ROW-2"]
    assert position["ROW-1"]["children"] == ["CHART-1", "CHART-2"]
    assert position["CHART-3"]["parents"] == ["ROOT_ID", "GRID_ID", "ROW-2"]


def test_full_sync_builds_governed_datasets_dashboards_and_row_security(product: DataProduct) -> None:
    api = FakeSuperset()
    databases = ensure_databases(
        api,
        pg_user="u",
        pg_password="p",  # ruff: ignore[hardcoded-password-func-arg] — a fake Superset, no real credential
        pg_database="data_platform",  # type: ignore[arg-type]
        clickhouse_user="ch",
        clickhouse_password="x",  # ruff: ignore[hardcoded-password-func-arg]
    )
    assert set(databases) == {WAREHOUSE_DB, MARTS_DB}
    urls = sync_product(api, product, databases, "demo")  # type: ignore[arg-type]
    assert urls == [
        "/superset/dashboard/retail-sales/",
        "/superset/dashboard/retail-products/",
        "/superset/dashboard/retail-live/",
    ]
    daily = api.find("dataset", "table_name", "mart_sales_daily")
    assert daily is not None and daily["database"]["id"] == databases[MARTS_DB]
    revenue = next(m for m in daily["metrics"] if m["metric_name"] == "revenue")
    assert revenue["expression"] == "(sum(revenue_cents)) / 100.0"
    hot = api.find("dataset", "table_name", "hot_sales_minute")
    assert hot is not None and hot["database"]["id"] == databases[WAREHOUSE_DB]
    board = api.find("dashboard", "slug", "retail-live")
    assert board is not None and json.loads(board["json_metadata"])["refresh_frequency"] == 10
    rules = {r["name"]: r for r in api.store["rowlevelsecurity"]}
    assert rules["surf_retail: demo tenant only"]["clause"] == "org_id = 'demo'"
    assert rules["Store manager S001 (Tarifa)"]["clause"] == "store_id = 'S001'"
    assert api.store["security/users"][0]["username"] == "store.manager.s001"
    charts_before = len(api.store["chart"])
    sync_product(api, product, databases, "demo")  # type: ignore[arg-type]
    assert len(api.store["chart"]) == charts_before  # idempotent
