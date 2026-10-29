"""Record lookup, search, dataset rows and the transform trail against a fake warehouse."""

from __future__ import annotations

from typing import Any

import pytest
from ai_circus_shared.data_policy import AccessDeniedError

from data_api.core import records
from data_api.core.records import NotFoundError
from tests.conftest_products import access_as, retail
from tests.fakes import FakeWarehouse

PAYLOAD = {
    "receipt_id": "S012-R01-000000001",
    "store_id": "S012",
    "ts": "2026-10-28T10:00:00+00:00",
    "produced_at": "2026-10-28T10:00:00.2+00:00",
    "loyalty_id": "LOY-00000001",
    "channel": "store",
    "totals": {"grand_total_cents": 1234},
}


def bronze_handler(sql: str, _params: dict[str, Any]) -> list[dict[str, Any]]:
    if "payload_hash" in sql:
        return [
            {
                "version": 1,
                "payload": PAYLOAD,
                "payload_hash": "h",
                "event_ts": PAYLOAD["ts"],
                "received_at": "2026-10-28T10:00:01+00:00",
                "source_kind": "stream",
                "delivery": None,
                "contract_version": "1.0",
                "corrections": 0,
                "archived_at": None,
            }
        ]
    if "DISTINCT ON" in sql:
        return [
            {
                "business_key": "S012-R01-000000001",
                "version": 1,
                "payload": PAYLOAD,
                "event_ts": PAYLOAD["ts"],
                "received_at": "2026-10-28T10:00:01+00:00",
                "source_kind": "stream",
            }
        ]
    return []


@pytest.fixture(autouse=True)
def curated_at_eleven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeWarehouse, "last_transform", lambda *_a: {"curated_through": "2026-10-28T11:00:00+00:00"})


def test_record_has_trail_and_masks_for_store_manager() -> None:
    warehouse = FakeWarehouse(bronze_handler)
    access = access_as("retail_store_manager", values=("S012",))
    record = records.get_record(warehouse, retail(), access, "receipt", "S012-R01-000000001")  # type: ignore[arg-type]
    assert "loyalty_id" not in record["current"]["payload"]
    assert record["current"]["trail"]["gold"] == "2026-10-28T11:00:00+00:00"
    assert record["current"]["summary"]["Store"] == "S012"
    sql, params = warehouse.queries[0]
    assert "filter_value::text = ANY" in sql and params["p0"] == "acme"


def test_admin_sees_the_raw_payload_and_unknown_entity_404() -> None:
    record = records.get_record(FakeWarehouse(bronze_handler), retail(), access_as(), "receipt", "S012-R01-000000001")  # type: ignore[arg-type]
    assert record["current"]["payload"]["loyalty_id"] == "LOY-00000001"
    with pytest.raises(NotFoundError):
        records.get_record(FakeWarehouse(), retail(), access_as(), "receipt", "missing")  # type: ignore[arg-type]
    with pytest.raises(NotFoundError):
        records.record_spec(retail(), "invoice")


def test_search_uses_full_text_and_key_prefix() -> None:
    warehouse = FakeWarehouse(bronze_handler)
    rows = records.list_records(warehouse, retail(), access_as(), "receipt", "SPRING-10", 10)  # type: ignore[arg-type]
    assert rows[0]["key"] == "S012-R01-000000001"
    sql, params = warehouse.queries[0]
    assert "plainto_tsquery" in sql and "SPRING-10%" in params.values()


def test_analyst_has_no_bronze_and_steward_sees_quarantine() -> None:
    with pytest.raises(AccessDeniedError):
        records.get_record(FakeWarehouse(), retail(), access_as("retail_analyst"), "receipt", "x")  # type: ignore[arg-type]
    warehouse = FakeWarehouse(lambda _s, _p: [{"quarantine_id": 1, "payload": {"loyalty_id": "L", "a": 1}}])
    rows = records.quarantine_list(warehouse, retail(), access_as("retail_data_steward"), "open", 10)  # type: ignore[arg-type]
    assert rows[0]["payload"] == {"a": 1}


def test_dataset_rows_drop_hidden_columns() -> None:
    warehouse = FakeWarehouse(
        lambda _s, _p: [{"day": "2026-10-28"}], columns=["org_id", "day", "store_id", "customer_key"]
    )
    result = records.dataset_rows(
        warehouse,
        retail(),
        access_as("retail_store_manager", values=("S012",)),  # type: ignore[arg-type]
        "mart_sales_daily",
        10,
    )
    assert "customer_key" not in result["columns"]
    with pytest.raises(NotFoundError):
        records.dataset_rows(warehouse, retail(), access_as(), "nope", 10)  # type: ignore[arg-type]


def test_row_filter_options_label_only_the_rows_the_caller_sees() -> None:
    """A store manager learns only their own store's name; an admin viewing as that manager
    still gets every store for the picker.
    """
    from ai_circus_shared.auth import Identity
    from ai_circus_shared.data_policy import resolve_access

    warehouse = FakeWarehouse(
        lambda _s, _p: [{"value": "S012", "label": "Acme Málaga"}, {"value": None, "label": "?"}],
        columns=["org_id", "store_id", "store_name", "lat", "lon"],
    )
    manager = Identity(
        subject="m", org_id="acme", roles=frozenset({"retail_store_manager"}), attributes=(("stores", ("S012",)),)
    )
    access = resolve_access(manager, retail().config.access, data_org_id="acme", is_platform=False)
    assert records.row_filter_options(warehouse, retail(), access) == [{"value": "S012", "label": "Acme Málaga"}]  # type: ignore[arg-type]
    sql, params = warehouse.queries[-1]
    assert "mart_stores" in sql and ["S012"] in params.values()

    records.row_filter_options(warehouse, retail(), access_as("retail_store_manager", values=("S012",)))  # type: ignore[arg-type]
    sql, params = warehouse.queries[-1]
    assert ["S012"] not in params.values()  # viewing as: the admin's own (unfiltered) list

    no_label = FakeWarehouse(columns=["org_id", "store_id"])
    assert records.row_filter_options(no_label, retail(), access) == []  # type: ignore[arg-type]


def test_dataset_rows_filter_by_visible_columns_only() -> None:
    """`equals` binds its values and only accepts columns the role can see."""
    warehouse = FakeWarehouse(columns=["org_id", "sku", "rec_sku", "lift", "customer_key"])
    records.dataset_rows(warehouse, retail(), access_as(), "mart_sales_daily", 8, {"sku": "FSH-0102"})  # type: ignore[arg-type]
    sql, params = warehouse.queries[-1]
    assert 'CAST("sku" AS text) IN (:p' in sql and "FSH-0102" in params.values() and "FSH-0102" not in sql
    records.dataset_rows(warehouse, retail(), access_as(), "mart_sales_daily", 8, {"lift": "True"})  # type: ignore[arg-type]
    sql, params = warehouse.queries[-1]
    assert {"true", "1"} <= set(params.values())  # a boolean filter matches Postgres and ClickHouse spellings
    analyst = access_as("retail_analyst")
    with pytest.raises(NotFoundError):
        records.dataset_rows(warehouse, retail(), analyst, "mart_sales_daily", 8, {"nope": "x"})  # type: ignore[arg-type]
