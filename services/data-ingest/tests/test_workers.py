"""Tests for the stream, landing, CDC and warehouse workers against in-memory fakes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from data_ingest.core.cdc_capture import CdcWorker
from data_ingest.core.files import FileLoader
from data_ingest.core.products import DataProduct, load_products
from data_ingest.core.stream import StreamWorker
from data_ingest.core.warehouse import bootstrap_product
from tests.fakes import FakeEngine, FakeEvents, FakeMessage, FakeStore
from tests.test_bronze import receipt

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


@pytest.fixture(scope="module")
def product() -> DataProduct:
    return load_products(SCENARIOS, "surf_retail")["surf_retail"]


def upsert_returns_all_inserted(sql: str, params: Any) -> list[tuple[Any, ...]] | None:
    if "INSERT INTO" in sql and "bronze_" in sql and "RETURNING" in sql:
        return [(o, k, v, True) for o, k, v in zip(params["o"], params["k"], params["v"], strict=True)]
    if "SELECT DISTINCT row" in sql:
        return []  # no reference data captured yet: known_value accepts
    return None


class FakeConsumer:
    """Never consumed in these tests: the stream is driven through _process directly."""

    pass


def make_stream(product: DataProduct, engine: FakeEngine, events: FakeEvents) -> StreamWorker:
    return StreamWorker(FakeConsumer(), engine, {product.slug: product}, events)  # type: ignore[arg-type]


def test_bootstrap_creates_every_table(product: DataProduct) -> None:
    engine = FakeEngine()
    bootstrap_product(engine, product)  # type: ignore[arg-type]
    sql = "\n".join(s for s, _ in engine.statements)
    for table in (
        "bronze_pos_receipts",
        "bronze_web_orders",
        "hot_sales_minute",
        "bronze_cdc_products",
        "cdc_stores",
        "ops_quarantine",
        "ops_file_deliveries",
    ):
        assert table in sql
    assert "pg_advisory_xact_lock" in engine.statements[0][0]


def test_stream_routes_by_topic_and_tenant(product: DataProduct) -> None:
    engine, events = FakeEngine(upsert_returns_all_inserted), FakeEvents()
    stream = make_stream(product, engine, events)
    bad = receipt(receipt_id="S012-R03-000000009")
    bad["totals"] = {**bad["totals"], "grand_total_cents": 1}
    stream._process([
        FakeMessage("tenant-acme.retail.pos.receipts.v1", receipt()),
        FakeMessage("tenant-acme.retail.pos.receipts.v1", bad),
        FakeMessage("tenant-acme.retail.pos.receipts.v1", b"{broken"),
        FakeMessage("retail.pos.receipts.v1", receipt()),  # not a tenant topic: ignored
        FakeMessage("tenant-acme.unknown.topic.v1", receipt()),  # no route: ignored
    ])
    upserts = engine.sql("bronze_pos_receipts")
    assert upserts and upserts[0][1]["o"] == ["acme"]
    quarantined = engine.sql("ops_quarantine")[0][1]
    assert len(quarantined) == 2 and {q["org_id"] for q in quarantined} == {"acme"}
    assert engine.sql("hot_sales_minute")
    stats = stream.stats["surf_retail.pos_receipts"]
    assert (stats["received"], stats["new"], stats["quarantined"]) == (3, 1, 2)
    stages = {(r[2], r[3]) for r in events.records}
    assert {("bronze", "ok"), ("hot", "approximate"), ("validated", "quarantined")} <= stages


def test_landing_delivery_is_loaded_reconciled_and_moved(product: DataProduct) -> None:
    engine, events = FakeEngine(upsert_returns_all_inserted), FakeEvents()
    stream = make_stream(product, engine, events)
    folder = "landing/surf_retail/legacy_pos/date=2026-10-27/store=S019"
    receipts = [receipt(receipt_id=f"S019-R01-00000000{i}", store_id="S019", source="legacy_file") for i in (1, 2)]
    control = {"store_id": "S019", "business_date": "2026-10-27", "receipt_count": 3, "grand_total_cents": 3 * 4837}
    store = FakeStore({
        ("acme", f"{folder}/receipts.jsonl"): "\n".join(json.dumps(r) for r in receipts).encode(),
        ("acme", f"{folder}/_control.json"): json.dumps(control).encode(),
    })
    loader = FileLoader(store, engine, {product.slug: product}, stream, events, 60)  # type: ignore[arg-type]
    assert loader.scan() == 1
    delivery = engine.sql("ops_file_deliveries")[0][1]
    assert (delivery["status"], delivery["ec"], delivery["lc"]) == ("gap", 3, 2)
    assert delivery["business_date"].isoformat() == "2026-10-27"
    assert not store.exists("acme", f"{folder}/_control.json")
    assert store.exists("acme", "landing/surf_retail/processed/legacy_pos/date=2026-10-27/store=S019/_control.json")
    assert loader.scan() == 0  # moved: never loaded twice


def test_cdc_poll_writes_change_log_and_current_state(product: DataProduct) -> None:
    lines = [
        ("0/1", "BEGIN 1"),
        (
            "0/2",
            "table surf_retail.products: UPDATE: org_id[text]:'acme' sku[text]:'KIT-0042' "
            "price_cents[integer]:1099 name[text]:null",
        ),
        ("0/3", "table surf_retail.pos_sequences: UPDATE: org_id[text]:'acme' store_id[text]:'S012'"),
        ("0/4", "COMMIT 1"),
    ]
    sources = FakeEngine(lambda sql, _p: lines if "pg_logical_slot_get_changes" in sql else None)
    warehouse, events = FakeEngine(), FakeEvents()
    worker = CdcWorker(sources, warehouse, {product.slug: product}, events, 5)  # type: ignore[arg-type]
    assert worker.poll() == 1  # pos_sequences is not a declared CDC table
    log = warehouse.sql("bronze_cdc_products")[0][1][0]
    assert (log["org_id"], log["key"], log["op"], log["lsn"]) == ("acme", "KIT-0042", "UPDATE", "0/2")
    assert json.loads(log["row"])["name"] is None
    assert warehouse.sql('"cdc_products"')
    assert ("acme", "surf_retail", "bronze", "ok", "KIT-0042") in events.records


def test_cdc_snapshot_when_slot_is_new(product: DataProduct) -> None:
    def sources_handler(sql: str, _params: Any) -> list[tuple[Any, ...]] | None:
        if "to_regclass" in sql:
            return [("exists",)]
        if "to_jsonb" in sql:
            return [({"org_id": "acme", "store_id": "S001", "name": "Acme Bilbao"},)]
        return None

    sources, warehouse = (
        FakeEngine(sources_handler),
        FakeEngine(lambda sql, _p: [(False,)] if "NOT EXISTS" in sql else None),
    )
    worker = CdcWorker(sources, warehouse, {product.slug: product}, FakeEvents(), 5)  # type: ignore[arg-type]
    worker._snapshot(force=True)
    snapshot_rows = warehouse.sql("bronze_cdc_stores")[0][1]
    assert snapshot_rows[0]["op"] == "SNAPSHOT" and snapshot_rows[0]["key"] == "S001"


def test_large_delivery_is_landed_in_bounded_batches(product: DataProduct, monkeypatch: pytest.MonkeyPatch) -> None:
    """A month of history is landed a batch at a time (bounded memory) and still reconciles."""
    from data_ingest.core import files

    monkeypatch.setattr(files, "INGEST_BATCH", 2)
    engine, events = FakeEngine(upsert_returns_all_inserted), FakeEvents()
    stream = make_stream(product, engine, events)
    calls: list[int] = []
    original = stream.ingest
    monkeypatch.setattr(
        stream, "ingest", lambda p, t, items, **kw: calls.append(len(items)) or original(p, t, items, **kw)
    )
    folder = "landing/surf_retail/history_pos/date=2026-09-01/store=S001"
    receipts = [receipt(receipt_id=f"S001-R01-00000000{i}", store_id="S001") for i in range(5)]
    control = {"record_count": 5, "grand_total_cents": 5 * 4837}
    store = FakeStore({
        ("acme", f"{folder}/receipts.jsonl"): "\n".join(json.dumps(r) for r in receipts).encode(),
        ("acme", f"{folder}/_control.json"): json.dumps(control).encode(),
    })
    loader = FileLoader(store, engine, {product.slug: product}, stream, events, 60)  # type: ignore[arg-type]
    assert loader.scan() == 1
    assert calls == [2, 2, 1]
    delivery = engine.sql("ops_file_deliveries")[0][1]
    assert (delivery["status"], delivery["ec"], delivery["lc"]) == ("reconciled", 5, 5)
