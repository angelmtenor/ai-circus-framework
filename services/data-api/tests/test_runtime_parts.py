"""Simulation sinks, the simulation manager's action guard, the events relay and Query."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

import pytest

from data_api.core.events_relay import EventsRelay
from data_api.core.runtime import parse_tier_urls
from data_api.core.simulation import KafkaSink, LandingSink, SimulationManager
from data_api.core.warehouse import ClickHouseReader, Query
from tests.conftest_products import retail


class FakeProducer:
    """Collects produced messages."""

    def __init__(self) -> None:
        """Start with no messages."""
        self.messages: list[tuple[str, bytes, bytes | None]] = []

    def produce(self, topic: str, value: bytes, key: bytes | None = None, headers: Any = None) -> None:
        """Collect one message."""
        self.messages.append((topic, value, key))

    def poll(self, _timeout: float) -> int:
        """Nothing to serve."""
        return 0

    def flush(self, _timeout: float) -> int:
        """Nothing to flush."""
        return 0


class FakeStore:
    """An in-memory object store."""

    def __init__(self) -> None:
        """Start empty."""
        self.objects: dict[tuple[str, str], bytes] = {}

    def put(self, org: str, key: str, data: bytes) -> str:
        """Store one object."""
        self.objects[org, key] = data
        return key


def test_kafka_sink_publishes_to_the_tenant_topic_keyed_by_business_key() -> None:
    producer = FakeProducer()
    sink = KafkaSink(producer, retail(), "acme")  # type: ignore[arg-type]
    sink.publish("pos_receipts", {"receipt_id": "S012-R01-000000001", "contract_version": "1.0"})
    topic, value, key = producer.messages[0]
    assert topic == "tenant-acme.retail.pos.receipts.v1"
    assert key == b"S012-R01-000000001" and json.loads(value)["receipt_id"] == "S012-R01-000000001"


def test_landing_sink_writes_data_before_control() -> None:
    store = FakeStore()
    folder = LandingSink(store, retail(), "acme").drop(  # type: ignore[arg-type]
        "legacy_pos_files", "date=2026-10-27/store=S019", [{"a": 1}, {"a": 2}], {"receipt_count": 2}
    )
    assert folder == "landing/surf_retail/legacy_pos/date=2026-10-27/store=S019"
    assert list(store.objects) == [("acme", f"{folder}/receipts.jsonl"), ("acme", f"{folder}/_control.json")]


def test_simulation_manager_only_runs_declared_actions_with_declared_params() -> None:
    manager = SimulationManager({"surf_retail": retail()}, FakeProducer(), FakeStore(), None)  # type: ignore[arg-type]
    seen: dict[str, Any] = {}

    class Plugin:
        def setup(self, ctx: Any) -> None:
            seen["setup"] = ctx.org_id

        def act(self, ctx: Any, action: str, params: dict[str, Any], now: Any) -> dict[str, Any]:
            seen["params"] = params
            return {"record_id": "x"}

    manager._plugins["surf_retail"] = Plugin()  # type: ignore[assignment]
    with pytest.raises(LookupError):
        manager.act(retail(), "acme", "drop_tables", {})
    result = manager.act(retail(), "acme", "sell", {"store_id": "S012", "evil": "x"})
    assert seen == {"setup": "acme", "params": {"store_id": "S012"}} and result["record_id"] == "x"
    assert manager.status(retail(), "acme")["last_actions"][0]["action"] == "sell"


def test_events_relay_buffers_and_fans_out_per_tenant() -> None:
    relay = EventsRelay("localhost:1")

    async def scenario() -> list[dict[str, Any]]:
        mine = relay.subscribe("acme", "surf_retail")
        other = relay.subscribe("globex", "surf_retail")
        relay.publish_local("acme", {"slug": "surf_retail", "stage": "bronze"})
        await asyncio.sleep(0)
        assert other.queue.empty()
        relay.unsubscribe(other)
        return [await mine.queue.get()]

    assert asyncio.run(scenario()) == [{"slug": "surf_retail", "stage": "bronze"}]
    assert relay.recent("acme", "surf_retail") == [{"slug": "surf_retail", "stage": "bronze"}]
    assert relay.recent("globex", "surf_retail") == []


def test_query_placeholders_per_dialect() -> None:
    pg, ch = Query("postgres"), Query("clickhouse")
    assert pg.param("x") == ":p0" and pg.param(["a"], array=True) == "CAST(:p1 AS text[])"
    assert ch.param("x") == "{p0:String}" and ch.param(date(2026, 1, 1)) == "{p1:Date}"
    assert ch.ident("a", "b") == "`a`.`b`" and pg.ident("a") == '"a"'
    assert "IN {p2:Array(String)}" in ch.in_list("col", ("v",))


def test_parse_tier_urls() -> None:
    assert parse_tier_urls("analytics=http://a:1, bi=http://b ,bad") == {"analytics": "http://a:1", "bi": "http://b"}


def test_clickhouse_reader_shares_one_client_without_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requests share the reader from a threadpool; a session would make ClickHouse refuse
    concurrent queries ("Attempt to execute concurrent queries within the same session").
    """
    import clickhouse_connect

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **kwargs: calls.append(kwargs) or object())
    ClickHouseReader("http://clickhouse:8123", None, None)
    assert calls == [
        {"dsn": "http://clickhouse:8123", "username": "default", "password": "", "autogenerate_session_id": False}
    ]
