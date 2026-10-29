"""Feature marts exported as the raw datasets of the tabular_ml scenarios they train."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from data_transform.core.ml_export import export_features
from data_transform.core.products import load_products
from tests.fakes import FakeEngine

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


class FakeStore:
    """Collects uploads per bucket."""

    def __init__(self, bucket: str, sink: dict[str, bytes]) -> None:
        """Bind a bucket to a shared sink."""
        self.bucket, self.sink = bucket, sink

    def put(self, org: str, key: str, data: bytes) -> str:
        """Store one object."""
        self.sink[f"{self.bucket}/{org}/{key}"] = data
        return key


def test_every_ml_export_is_written_per_tenant_with_its_scenarios_columns() -> None:
    product = load_products(SCENARIOS, "surf_retail")["surf_retail"]
    sink: dict[str, bytes] = {}
    queries: list[tuple[str, Any]] = []

    def handler(sql: str, params: Any) -> list[tuple[Any, ...]] | None:
        queries.append((sql, params))
        if "ml_demand_features" in sql and params["org"] == "demo":
            return [("S001|sup|2026-10-19", "2026-10-19", "S001", "sup", *[1] * 10, 12)]
        return []

    keys = export_features(
        FakeEngine(handler),  # type: ignore[arg-type]
        lambda bucket: FakeStore(bucket, sink),  # type: ignore[arg-type,return-value]
        product,
        SCENARIOS,
        ["demo", "acme"],
    )
    assert keys == ["raw/surf_demand_weekly.csv"]  # acme has no rows: nothing written for it
    sql = next(q for q, _ in queries if "ml_demand_features" in q)
    assert '"units_next_week" IS NOT NULL' in sql and "org_id = :org" in sql
    rows = list(csv.reader(io.StringIO(sink["scenario-surf-demand/demo/raw/surf_demand_weekly.csv"].decode())))
    assert rows[0][:4] == ["row_id", "week", "store_id", "rider_group"] and rows[0][-1] == "units_next_week"
    assert len(rows) == 2
