#!/usr/bin/env python3
"""Synthetic data generator for the `luznova_regional_demand` scenario.

Produces `scenarios/luznova_regional_demand/sample_data/luznova_regional_demand.csv` —
one row per (day, Comunidad Autónoma) for calendar year 2024 across all 17 of Spain's
mainland/island regions (real capital coordinates + real approximate 2023 INE
population figures; everything downstream of that — the actual demand values — is a
designed synthetic formula, not fitted to any real consumption dataset). This mirrors
`cnc_surface_finish`'s own precedent: real physical/geographic grounding, disclosed
synthetic outcome.

Demand model (per region, per day):
    baseline   = population_thousands * MWH_PER_1000_POP_PER_DAY
    industrial = baseline * (1 + industrial_index / 100 * INDUSTRIAL_MULTIPLIER)
    weather    = industrial * (1 + TEMP_CURVATURE * (mean_temperature_c - MILD_POINT_C) ** 2)
    calendar   = weather * (WEEKEND_FACTOR if is_weekend else 1.0) * (HOLIDAY_FACTOR if is_holiday else 1.0)
    demand_mwh = calendar * lognormal_noise

`mean_temperature_c` itself comes from a per-region seasonal sinusoid (real approximate
annual-mean temperature and seasonal swing per region) plus daily noise — so the
temperature -> demand "bathtub curve" (higher demand when it's cold *or* hot,
relative to a mild ~18°C point) is a genuine, learnable relationship, not noise.

Run: `python scripts/generate_luznova_regional_demand.py`
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

OUT_PATH = Path(__file__).resolve().parent.parent / "scenarios/luznova_regional_demand/sample_data/luznova_regional_demand.csv"
SEED = 42
YEAR = 2024

# key, label, lat/lon (real capital), population (thousands, ~2023 INE), industrial
# intensity index (0-100, synthetic proxy), annual-mean temperature (°C) and seasonal
# amplitude (°C) — all real-order-of-magnitude, not exact.
REGIONS = [
    {"key": "andalucia", "label": "Andalucía", "lat": 37.39, "lon": -5.99, "population_thousands": 8500, "industrial_index": 45, "avg_temp_c": 18.5, "temp_amplitude_c": 9.0},
    {"key": "cataluna", "label": "Cataluña", "lat": 41.39, "lon": 2.16, "population_thousands": 7700, "industrial_index": 75, "avg_temp_c": 15.5, "temp_amplitude_c": 8.5},
    {"key": "madrid", "label": "Comunidad de Madrid", "lat": 40.42, "lon": -3.70, "population_thousands": 6700, "industrial_index": 55, "avg_temp_c": 14.5, "temp_amplitude_c": 10.5},
    {"key": "valenciana", "label": "Comunitat Valenciana", "lat": 39.47, "lon": -0.38, "population_thousands": 5100, "industrial_index": 60, "avg_temp_c": 17.5, "temp_amplitude_c": 8.0},
    {"key": "galicia", "label": "Galicia", "lat": 42.88, "lon": -8.55, "population_thousands": 2700, "industrial_index": 35, "avg_temp_c": 13.5, "temp_amplitude_c": 5.5},
    {"key": "castilla_y_leon", "label": "Castilla y León", "lat": 41.65, "lon": -4.73, "population_thousands": 2380, "industrial_index": 30, "avg_temp_c": 11.0, "temp_amplitude_c": 10.5},
    {"key": "pais_vasco", "label": "País Vasco", "lat": 42.85, "lon": -2.67, "population_thousands": 2200, "industrial_index": 80, "avg_temp_c": 13.5, "temp_amplitude_c": 7.0},
    {"key": "castilla_la_mancha", "label": "Castilla-La Mancha", "lat": 39.86, "lon": -4.02, "population_thousands": 2050, "industrial_index": 25, "avg_temp_c": 14.0, "temp_amplitude_c": 11.0},
    {"key": "canarias", "label": "Canarias", "lat": 28.10, "lon": -15.41, "population_thousands": 2170, "industrial_index": 20, "avg_temp_c": 20.5, "temp_amplitude_c": 3.5},
    {"key": "murcia", "label": "Región de Murcia", "lat": 37.99, "lon": -1.13, "population_thousands": 1550, "industrial_index": 40, "avg_temp_c": 18.0, "temp_amplitude_c": 8.0},
    {"key": "aragon", "label": "Aragón", "lat": 41.65, "lon": -0.88, "population_thousands": 1330, "industrial_index": 40, "avg_temp_c": 13.5, "temp_amplitude_c": 10.5},
    {"key": "baleares", "label": "Illes Balears", "lat": 39.57, "lon": 2.65, "population_thousands": 1200, "industrial_index": 15, "avg_temp_c": 17.5, "temp_amplitude_c": 7.0},
    {"key": "extremadura", "label": "Extremadura", "lat": 38.92, "lon": -6.34, "population_thousands": 1060, "industrial_index": 20, "avg_temp_c": 16.0, "temp_amplitude_c": 10.5},
    {"key": "asturias", "label": "Asturias", "lat": 43.36, "lon": -5.85, "population_thousands": 1010, "industrial_index": 45, "avg_temp_c": 13.0, "temp_amplitude_c": 5.5},
    {"key": "navarra", "label": "Navarra", "lat": 42.82, "lon": -1.65, "population_thousands": 660, "industrial_index": 55, "avg_temp_c": 12.5, "temp_amplitude_c": 9.5},
    {"key": "cantabria", "label": "Cantabria", "lat": 43.46, "lon": -3.81, "population_thousands": 580, "industrial_index": 35, "avg_temp_c": 13.5, "temp_amplitude_c": 5.0},
    {"key": "la_rioja", "label": "La Rioja", "lat": 42.47, "lon": -2.45, "population_thousands": 320, "industrial_index": 40, "avg_temp_c": 13.0, "temp_amplitude_c": 9.5},
]

# Fixed-date Spanish national holidays observed for demand-suppression purposes
# (a simplification — real holidays include region-specific/moving dates too, not
# needed for this synthetic demo).
NATIONAL_HOLIDAYS = {(1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (11, 1), (12, 6), (12, 8), (12, 25)}

# Approx. real Spanish per-capita grid demand (~5.3 MWh/person/year across all
# sectors, not just households) expressed per 1,000 people per day.
MWH_PER_1000_POP_PER_DAY = 14.6
INDUSTRIAL_MULTIPLIER = 1.3
TEMP_CURVATURE = 0.0055
MILD_POINT_C = 18.0
WEEKEND_FACTOR = 0.90
HOLIDAY_FACTOR = 0.85
NOISE_SIGMA = 0.05


def main() -> None:
    rng = np.random.default_rng(SEED)
    dates = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31", freq="D")
    rows = []
    row_id = 0
    for day in dates:
        day_of_year = day.dayofyear
        is_weekend = day.weekday() >= 5
        is_holiday = (day.month, day.day) in NATIONAL_HOLIDAYS
        for region in REGIONS:
            # Coldest around day 15 (mid-January); seasonal cosine peaks there.
            seasonal = math.cos(2 * math.pi * (day_of_year - 15) / 365.25)
            temp = region["avg_temp_c"] - region["temp_amplitude_c"] * seasonal + rng.normal(0, 1.4)

            baseline = region["population_thousands"] * MWH_PER_1000_POP_PER_DAY
            industrial = baseline * (1 + region["industrial_index"] / 100 * INDUSTRIAL_MULTIPLIER)
            weather = industrial * (1 + TEMP_CURVATURE * (temp - MILD_POINT_C) ** 2)
            calendar = weather * (WEEKEND_FACTOR if is_weekend else 1.0) * (HOLIDAY_FACTOR if is_holiday else 1.0)
            demand = calendar * math.exp(rng.normal(0, NOISE_SIGMA))

            rows.append(
                {
                    "row_id": f"{day.strftime('%Y%m%d')}_{region['key']}",
                    "region": region["key"],
                    "month": day.month,
                    "is_weekend": int(is_weekend),
                    "is_holiday": int(is_holiday),
                    "mean_temperature_c": round(temp, 1),
                    "population_thousands": region["population_thousands"],
                    "industrial_index": region["industrial_index"],
                    "demand_mwh": round(demand, 1),
                }
            )
            row_id += 1

    df = pd.DataFrame(rows)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(df)} rows to {OUT_PATH}")
    print(df["demand_mwh"].describe())


if __name__ == "__main__":
    main()
