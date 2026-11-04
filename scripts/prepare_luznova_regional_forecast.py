#!/usr/bin/env python3
"""Regenerate scenarios/luznova_regional_forecast/sample_data/luznova_regional_forecast.csv from
luznova_regional_demand's committed sample (docs/adr/0016-time-series-forecasting.md).

Same synthetic rows, as series: the date comes out of `row_id` ("20240101_sevilla"), and each of the
50 provinces has a reading every second day of 2024. `month`, `is_weekend` and `is_holiday` are
dropped: the forecaster builds its own calendar features, and a holiday is not a value it could
assume from the previous season. Deterministic: run it again and the CSV is byte-identical.

    services/training/.venv/bin/python scripts/prepare_luznova_regional_forecast.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios"
SOURCE = SCENARIOS / "luznova_regional_demand" / "sample_data" / "luznova_regional_demand.csv"
OUT = SCENARIOS / "luznova_regional_forecast" / "sample_data" / "luznova_regional_forecast.csv"
COLUMNS = ["date", "province", "population_thousands", "industrial_index", "mean_temperature_c", "demand_mwh"]


def build() -> pd.DataFrame:
    raw = pd.read_csv(SOURCE)
    raw["date"] = pd.to_datetime(raw["row_id"].str.slice(0, 8), format="%Y%m%d")
    series = raw[COLUMNS].sort_values(["province", "date"], kind="stable").reset_index(drop=True)
    expected = pd.date_range(series["date"].min(), series["date"].max(), freq="2D")
    for province, rows in series.groupby("province"):
        if not (pd.DatetimeIndex(rows["date"]) == expected).all():
            raise ValueError(f"{province} is not a reading every second day")
    return series


def main() -> None:
    df = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, date_format="%Y-%m-%d")
    print(f"wrote {OUT}: {len(df)} rows, {df['province'].nunique()} provinces, {df['date'].min()} -> {df['date'].max()}")


if __name__ == "__main__":
    main()
