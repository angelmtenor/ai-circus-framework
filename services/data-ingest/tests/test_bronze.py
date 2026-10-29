"""Tests for the contract → bronze classification and hot-path increments, on the real
surf_retail data product (its scenario.yaml and contracts).
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from data_ingest.core.bronze import Accepted, Rejected, classify, hot_increments, payload_hash
from data_ingest.core.files import partition_of, reconcile
from data_ingest.core.products import DataProduct, load_products
from data_ingest.core.stream import topic_patterns

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


@pytest.fixture(scope="module")
def product() -> DataProduct:
    return load_products(SCENARIOS, "surf_retail")["surf_retail"]


def receipt(**overrides: Any) -> dict[str, Any]:
    ts = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="milliseconds")
    body: dict[str, Any] = {
        "event_id": "S012-000000001-abcdef01",
        "event_type": "pos.receipt.created",
        "contract_version": "1.0",
        "source": "pos",
        "ts": ts,
        "produced_at": ts,
        "receipt_id": "S012-R03-000000001",
        "store_id": "S012",
        "register_id": "R03",
        "cashier_id": "C0458",
        "channel": "store",
        "currency": "EUR",
        "loyalty_id": "LOY-00000042",
        "lines": [
            {
                "line_no": 1,
                "sku": "KIT-0042",
                "qty": 2,
                "unit_price_cents": 1299,
                "discount_cents": 260,
                "promo_id": "SPRING-10",
                "tax_rate": 0.21,
                "line_total_cents": 2338,
            },
            {
                "line_no": 2,
                "sku": "TEX-0310",
                "qty": 1,
                "unit_price_cents": 2499,
                "discount_cents": 0,
                "promo_id": None,
                "tax_rate": 0.21,
                "line_total_cents": 2499,
            },
        ],
        "taxes": [{"rate": 0.21, "base_cents": 3997, "amount_cents": 840}],
        "payments": [{"method": "card", "amount_cents": 4837}],
        "totals": {"gross_cents": 5097, "discount_cents": 260, "grand_total_cents": 4837},
        "notes": None,
    }
    body.update(overrides)
    return body


def test_valid_receipt_is_accepted_with_key_filter_and_search_text(product: DataProduct) -> None:
    source = product.config.source("pos_receipts")
    outcome = classify(product, source, "admin", receipt())
    assert isinstance(outcome, Accepted)
    assert outcome.key == "S012-R03-000000001"
    assert outcome.version == 1
    assert outcome.filter_value == "S012"
    assert "KIT-0042" in outcome.search_text and "SPRING-10" in outcome.search_text


def test_lines_not_adding_up_is_quarantined_with_every_violation(product: DataProduct) -> None:
    bad = receipt()
    bad["totals"] = {**bad["totals"], "grand_total_cents": 5337}
    outcome = classify(product, product.config.source("pos_receipts"), "admin", bad)
    assert isinstance(outcome, Rejected)
    rules = {v["rule"] for v in outcome.violations}
    assert rules == {"lines_sum_to_total", "payments_sum_to_total"}
    assert outcome.key == "S012-R03-000000001"


def test_schema_violation_and_invalid_json(product: DataProduct) -> None:
    source = product.config.source("pos_receipts")
    outcome = classify(product, source, "admin", receipt(store_id="Madrid"))
    assert isinstance(outcome, Rejected) and outcome.violations[0]["rule"] == "schema"
    garbage = classify(product, source, "admin", None, raw="{not json")
    assert isinstance(garbage, Rejected) and garbage.raw == "{not json"


def test_known_value_rule_uses_the_lookup(product: DataProduct) -> None:
    source = product.config.source("pos_receipts")
    outcome = classify(product, source, "admin", receipt(), lookup=lambda table, column, value: value != "S012")
    assert isinstance(outcome, Rejected)
    assert [v["rule"] for v in outcome.violations] == ["known_store"]


def test_future_event_is_quarantined(product: DataProduct) -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    outcome = classify(product, product.config.source("pos_receipts"), "admin", receipt(ts=future))
    assert isinstance(outcome, Rejected) and outcome.violations[0]["rule"] == "not_in_future"


def test_payload_hash_ignores_key_order() -> None:
    assert payload_hash({"a": 1, "b": [1, 2]}) == payload_hash({"b": [1, 2], "a": 1})
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})


def test_hot_increments_count_and_sum_per_minute(product: DataProduct) -> None:
    source = product.config.source("pos_receipts")
    first = classify(product, source, "admin", receipt())
    second = classify(product, source, "admin", receipt(receipt_id="S012-R03-000000002"))
    assert isinstance(first, Accepted) and isinstance(second, Accepted)
    increments = hot_increments(product, source, [first, second])
    assert len(increments) == 1
    (aggregate, org, _minute, dims), values = next(iter(increments.items()))
    assert (aggregate, org, dims) == ("sales_minute", "admin", ("S012", "store"))
    assert values == [2, 2 * 4837, 2 * 3]  # receipts, revenue_cents, units


def test_hot_path_counts_only_first_version_of_a_versioned_source(product: DataProduct) -> None:
    source = product.config.source("web_orders")
    base = {
        "payload": {"store_id": None, "channel": "online", "totals": {"grand_total_cents": 100}, "lines": [{"qty": 1}]},
        "payload_hash": "x",
        "filter_value": None,
        "search_text": "",
    }
    now = datetime.now(UTC).isoformat()
    v1 = Accepted(org_id="admin", key="W-2026-0000001", version=1, event_ts=now, **copy.deepcopy(base))
    v2 = Accepted(org_id="admin", key="W-2026-0000001", version=2, event_ts=now, **copy.deepcopy(base))
    increments = hot_increments(product, source, [v1, v2])
    assert [values[0] for values in increments.values()] == [1]
    assert next(iter(increments))[3] == ("", "online")


@pytest.mark.parametrize(
    ("expected_count", "expected_sum", "loaded", "loaded_sum", "quarantined", "status"),
    [
        (10, 500, 10, 500, 0, "reconciled"),
        (10, 500, 9, 450, 0, "gap"),
        (10, 500, 10, 499, 0, "mismatch"),
        (10, 500, 10, 500, 1, "mismatch"),
        (None, None, 3, 30, 0, "no_control"),
    ],
)
def test_reconcile(
    expected_count: int | None, expected_sum: int | None, loaded: int, loaded_sum: int, quarantined: int, status: str
) -> None:
    assert reconcile(expected_count, expected_sum, loaded, loaded_sum, quarantined) == status


def test_partition_of_delivery_path() -> None:
    assert partition_of("date=2026-10-27/store=S019") == {"date": "2026-10-27", "store": "S019"}


def test_topic_patterns_match_only_tenant_topics(product: DataProduct) -> None:
    import re

    patterns = [re.compile(p) for p in topic_patterns({product.slug: product})]
    assert any(p.match("tenant-admin.retail.pos.receipts.v1") for p in patterns)
    assert not any(p.match("retail.pos.receipts.v1") for p in patterns)
    assert not any(p.match("tenant-admin.retail.pos.receipts.v1.extra") for p in patterns)
