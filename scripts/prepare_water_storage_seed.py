#!/usr/bin/env python3
"""Build the series file etl-tabular reads for the Reservoirs & Drought Watch scenario.

    PYTHONPATH=services/training/src:libs/shared/src services/training/.venv/bin/python scripts/prepare_water_storage_seed.py

Writes scenarios/water_reservoirs_drought/sample_data/water_storage_daily.csv.gz: one row per (day, series), for the
9 reservoirs and the 4 supply systems, from the committed snapshot: `pct` is the cleaned storage percentage (the
data-quality policy of config/quality.yaml applied), `precip_mm` and `temp_c` are the mean of the stations around the
series' reservoirs. etl-tabular refuses a series with a gap, so the few days that stay empty after the cleaning
(a system whose member has no reading that day) are carried forward, and the script says how many. Deterministic:
run it again and the bytes are identical.
"""

from __future__ import annotations

import gzip
import shutil
import tempfile
from pathlib import Path

import pandas as pd
from training.core.storage_series import build_series

SCENARIO = Path(__file__).resolve().parent.parent / "scenarios" / "water_reservoirs_drought"
OUT = SCENARIO / "sample_data" / "water_storage_daily.csv.gz"


def main() -> None:
    table, notes = build_series(SCENARIO)
    table = table.sort_values(["series", "day"]).reset_index(drop=True)
    empty = int(table["y"].isna().sum())
    grouped = table.groupby("series", sort=False)
    table["y"] = grouped["y"].ffill()
    table["precip_mm"] = table["precip_mm"].fillna(0.0)
    table["temp_c"] = grouped["temp_c"].ffill()
    table["temp_c"] = table.groupby("series", sort=False)["temp_c"].bfill()
    if table[["y", "precip_mm", "temp_c"]].isna().any().any():
        raise SystemExit("a series still has an empty value after carrying forward")
    out = pd.DataFrame({
        "date": table["day"].dt.strftime("%Y-%m-%d"),
        "series": table["series"],
        "pct": table["y"].round(2),
        "precip_mm": table["precip_mm"].round(1),
        "temp_c": table["temp_c"].round(1),
    })
    out = out.sort_values(["series", "date"], kind="stable")
    work = Path(tempfile.mkdtemp()) / "seed.csv"
    out.to_csv(work, index=False)
    with work.open("rb") as raw, OUT.open("wb") as handle, gzip.GzipFile("", "wb", 9, handle, 0) as packed:
        shutil.copyfileobj(raw, packed)
    print(f"{OUT}: {len(out)} rows, {out['series'].nunique()} series, {empty} empty days carried forward, "
          f"{OUT.stat().st_size / 1e6:.1f} MB; cleaning {notes['cleaning']}")  # fmt: skip


if __name__ == "__main__":
    main()
