#!/usr/bin/env python3
"""Regenerate scenarios/surf_ml/sample_data/surf_baskets.csv, straight from the surf_retail simulator's
day generators (same seed, same receipts as `make k3s-data-seed`), so the recommenders need no Data
Platform: a sample of a year's baskets, one row per basket and product.

Usage: services/training/.venv/bin/python scripts/prepare_surf_ml_samples.py [--end 2026-10-19] [--seed 7]
"""

from __future__ import annotations

import argparse
import sys
import zlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scenarios/surf_retail"))

from simulator import Catalogue, SurfSimulator, catalogue  # noqa: E402

OUT = ROOT / "scenarios/surf_ml/sample_data"
BASKET_DAYS = 365
# Every n-th basket by checksum: whole baskets, deterministic, under etl-tabular's 30,000 rows.
BASKET_SAMPLE_EVERY = 18


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default="2026-10-19", help="First day NOT generated.")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    end_day = date.fromisoformat(args.end)
    end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=UTC) - timedelta(seconds=1)
    first = end_day - timedelta(days=BASKET_DAYS)

    sim = SurfSimulator(args.seed)
    products = catalogue.build_products(args.seed)
    by_sku = {p.sku: p for p in products}
    cat = Catalogue(products, catalogue.build_promotions(products, first - timedelta(days=31), end_day))
    baskets: list[dict[str, object]] = []

    def add(doc_id: str, lines: list[dict[str, object]]) -> None:
        if zlib.crc32(doc_id.encode()) % BASKET_SAMPLE_EVERY:
            return
        for line in lines:
            product = by_sku[str(line["sku"])]
            baskets.append({
                "row_id": f"{doc_id}|{product.sku}",
                "basket_id": doc_id,
                "sku": product.sku,
                "product_name": product.name,
                "category": product.category,
                "rider_group": product.group,
            })

    for offset in range((end_day - first).days):
        day = first + timedelta(days=offset)
        for store in sim.stores:
            for receipt in sim._store_day(cat, store, day, end):
                add(receipt["receipt_id"], receipt["lines"])
        versions: dict[str, dict[str, object]] = {}
        for version in sim._web_day(cat, day, end):
            versions[str(version["order_id"])] = version  # versions come in order: the last one wins
        for order_id, order in versions.items():
            if order["status"] != "cancelled":
                add(order_id, order["lines"])  # type: ignore[arg-type]

    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(baskets).drop_duplicates("row_id").to_csv(OUT / "surf_baskets.csv", index=False)
    print(f"{len(baskets)} basket rows")


if __name__ == "__main__":
    main()
