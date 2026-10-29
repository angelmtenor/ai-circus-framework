"""Metric periods, filters, row filters and the comparison merge."""

from __future__ import annotations

from datetime import date

import pytest
from ai_circus_shared.data_policy import AccessDeniedError

from data_api.core.metrics import (
    MetricError,
    MetricRequest,
    catalogue,
    compare_range,
    parse_filters,
    period_range,
    query_metric,
)
from tests.conftest_products import access_as, retail
from tests.fakes import FakeWarehouse

TODAY = date(2026, 10, 28)  # a Wednesday


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        ("today", date(2026, 10, 28), date(2026, 10, 29)),
        ("yesterday", date(2026, 10, 27), date(2026, 10, 28)),
        ("this_week", date(2026, 10, 26), date(2026, 10, 29)),
        ("last_week", date(2026, 10, 19), date(2026, 10, 26)),
        ("last_7_days", date(2026, 10, 22), date(2026, 10, 29)),
    ],
)
def test_period_range(period: str, start: date, end: date) -> None:
    assert period_range(period, TODAY) == (start, end)


def test_custom_range_and_errors() -> None:
    assert period_range("all", TODAY, date(2026, 10, 1), date(2026, 10, 3)) == (date(2026, 10, 1), date(2026, 10, 4))
    with pytest.raises(MetricError):
        period_range("all", TODAY, date(2026, 10, 3), date(2026, 10, 1))
    with pytest.raises(MetricError):
        period_range("fortnight", TODAY)


def test_compare_ranges() -> None:
    start, end = date(2026, 10, 28), date(2026, 10, 29)
    assert compare_range("same_period_last_week", start, end) == (date(2026, 10, 21), date(2026, 10, 22))
    assert compare_range("previous_period", start, end) == (date(2026, 10, 27), date(2026, 10, 28))
    assert compare_range("none", start, end) is None


def test_parse_filters() -> None:
    assert parse_filters("store_id:S012, S013;channel:store;bad") == {
        "store_id": ["S012", "S013"],
        "channel": ["store"],
    }


def test_metric_query_scopes_tenant_and_row_filter_and_merges_comparison() -> None:
    def handler(sql: str, params: dict) -> list[dict]:
        if "GROUP BY" in sql:
            return [{"store_id": "S012", "value": 200 if date(2026, 10, 28) in params.values() else 100}]
        return [{"value": 200 if date(2026, 10, 28) in params.values() else 100}]

    warehouse = FakeWarehouse(handler)
    request = MetricRequest(metric="revenue", dimensions=("store_id",), period="today", compare="same_period_last_week")
    result = query_metric(warehouse, retail(), access_as("retail_store_manager", values=("S012",)), request, TODAY)  # type: ignore[arg-type]
    first_sql, first_params = warehouse.queries[0]
    assert "org_id = :p0" in first_sql and first_params["p0"] == "acme"
    assert '"store_id"::text = ANY(' in first_sql and ["S012"] in first_params.values()
    assert result["rows"][0]["change_pct"] == pytest.approx(100.0)
    assert result["compare"]["change_pct"] == pytest.approx(100.0)


def test_metric_rejects_undeclared_dimension_and_unbuilt_dataset() -> None:
    with pytest.raises(MetricError, match="cannot be split"):
        query_metric(
            FakeWarehouse(), retail(), access_as(), MetricRequest(metric="revenue", dimensions=("sku",)), TODAY
        )  # type: ignore[arg-type]
    with pytest.raises(MetricError, match="not been built"):
        query_metric(FakeWarehouse(columns=[]), retail(), access_as(), MetricRequest(metric="revenue"), TODAY)  # type: ignore[arg-type]


def test_filtered_role_cannot_read_a_dataset_without_its_filter_column() -> None:
    warehouse = FakeWarehouse(columns=["org_id", "week", "region", "sku", "units"])
    with pytest.raises(AccessDeniedError):
        query_metric(
            warehouse,
            retail(),
            access_as("retail_store_manager", values=("S012",)),  # type: ignore[arg-type]
            MetricRequest(metric="revenue"),
            TODAY,
        )


def test_catalogue_respects_layers() -> None:
    names = {m["name"] for m in catalogue(retail(), access_as("retail_data_steward"))}
    assert names == set()  # the steward sees ops/quarantine/bronze, no gold metrics
    assert "revenue" in {m["name"] for m in catalogue(retail(), access_as("retail_analyst"))}
