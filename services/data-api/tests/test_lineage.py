"""Lineage from the scenario's sources, the published dbt manifest and the metrics."""

from __future__ import annotations

import json
from typing import Any

from data_api.core import lineage
from tests.conftest_products import retail

MANIFEST = {
    "metadata": {"generated_at": "2026-10-28T12:00:00Z"},
    "nodes": {
        "model.r.stg_receipts": {
            "resource_type": "model",
            "name": "stg_receipts",
            "schema": "surf_retail_silver",
            "depends_on": {"nodes": ["source.r.bronze.bronze_pos_receipts"]},
        },
        "model.r.mart_sales_daily": {
            "resource_type": "model",
            "name": "mart_sales_daily",
            "schema": "surf_retail_gold",
            "depends_on": {"nodes": ["model.r.stg_receipts"]},
        },
        "test.r.unique_x": {"resource_type": "test", "depends_on": {"nodes": ["model.r.stg_receipts"]}},
        "seed.r.city_coordinates": {
            "resource_type": "seed",
            "name": "city_coordinates",
            "schema": "surf_retail_silver",
            "depends_on": {"nodes": []},
        },
        "model.r.mart_stores": {
            "resource_type": "model",
            "name": "mart_stores",
            "schema": "surf_retail_gold",
            "depends_on": {"nodes": ["seed.r.city_coordinates"]},
        },
    },
}


class FakeStore:
    """Serves one manifest."""

    def get(self, org: str, key: str) -> bytes:
        """The manifest of the platform pseudo-tenant."""
        assert (org, key) == ("platform", "dbt/surf_retail/postgres/manifest.json")
        return json.dumps(MANIFEST).encode()


def edges(graph: dict[str, Any]) -> set[tuple[str, str]]:
    return {(e["from"], e["to"]) for e in graph["edges"]}


def test_lineage_runs_from_topic_to_metric() -> None:
    lineage._cache.clear()
    graph = lineage.build(retail(), FakeStore())  # type: ignore[arg-type]
    found = edges(graph)
    assert ("topic:retail.pos.receipts.v1", "bronze:bronze_pos_receipts") in found
    assert ("files:legacy_pos_files", "bronze:bronze_pos_receipts") in found
    assert ("cdc:products", "bronze:bronze_cdc_products") in found
    assert ("bronze:bronze_pos_receipts", "silver:stg_receipts") in found
    assert ("silver:stg_receipts", "gold:mart_sales_daily") in found
    assert ("gold:mart_sales_daily", "metric:revenue") in found
    assert ("silver:city_coordinates", "gold:mart_stores") in found  # reference data kept as code
    assert {n["id"]: n["kind"] for n in graph["nodes"]}["silver:city_coordinates"] == "seed"
    tests = {n["id"]: n.get("tests") for n in graph["nodes"]}
    assert tests["silver:stg_receipts"] == 1
    assert graph["generated_at"] == "2026-10-28T12:00:00Z"


def test_lineage_without_a_manifest_still_shows_capture() -> None:
    lineage._cache.clear()
    graph = lineage.build(retail(), None)
    assert ("topic:retail.web.orders.v1", "bronze:bronze_web_orders") in edges(graph)
    assert not any(n["layer"] in ("silver", "gold") for n in graph["nodes"])
