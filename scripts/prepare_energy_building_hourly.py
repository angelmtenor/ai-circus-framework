#!/usr/bin/env python3
"""Regenerate scenarios/energy_building/sample_data/energy_building_hourly.csv — the energy_building
forecast scenario's seed (docs/adr/0016-time-series-forecasting.md).

The committed 10-minute file is a 10,000-row random sample of the UCI Appliances Energy Prediction
dataset (about half of every hour's six readings), so it cannot be forecast as it is: the gaps are
random. Averaging per hour leaves a regular hourly series — appliance energy per hour = mean of the
observed 10-minute readings x 6, outdoor weather = hourly mean — with only the hours where no
reading survived the sampling missing; those are filled by time interpolation (counted below).
Deterministic: run it again and the CSV is byte-identical.

    services/training/.venv/bin/python scripts/prepare_energy_building_hourly.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

SCENARIO = Path(__file__).resolve().parent.parent / "scenarios" / "energy_building" / "sample_data"
SOURCE = SCENARIO / "energy_building.csv"
OUT = SCENARIO / "energy_building_hourly.csv"
WEATHER = ["T_out", "RH_out", "Windspeed"]
READINGS_PER_HOUR = 6  # the source logs every 10 minutes


def build() -> tuple[pd.DataFrame, int]:
    raw = pd.read_csv(SOURCE, parse_dates=["date"])
    hourly = raw.set_index("date").sort_index()[["Appliances", *WEATHER]].resample("h").mean()
    missing = int(hourly["Appliances"].isna().sum())
    hourly = hourly.interpolate(method="time", limit_direction="both")
    hourly["Appliances"] = (hourly["Appliances"] * READINGS_PER_HOUR).round(1)
    hourly[WEATHER] = hourly[WEATHER].round(2)
    hourly.index.name = "date"
    return hourly.reset_index(), missing


def main() -> None:
    df, missing = build()
    df.to_csv(OUT, index=False, date_format="%Y-%m-%d %H:%M:%S")
    print(f"wrote {OUT}: {len(df)} hourly rows {df['date'].min()} -> {df['date'].max()}, {missing} hours interpolated")


if __name__ == "__main__":
    main()
