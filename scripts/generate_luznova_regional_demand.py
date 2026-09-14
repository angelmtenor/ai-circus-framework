#!/usr/bin/env python3
"""Synthetic data generator for the `luznova_regional_demand` scenario.

Produces `scenarios/luznova_regional_demand/sample_data/luznova_regional_demand.csv`
— one row per (day, provincia) across all 50 of Spain's provinces (real provincial-
capital coordinates + real approximate 2023 population figures per province, summing
to each Comunidad Autónoma's own real approximate total; everything downstream of
that — the actual demand values — is a designed synthetic formula, not fitted to any
real consumption dataset, same disclosure as `cnc_surface_finish`).

Each row carries BOTH `province` (50 categories, the real driver of population/
industrial_index below) and its parent `region` (17 categories, derived) — so the
same trained model serves ui-react's RegionMapView at either aggregation level
(region or province): a "region" map query pins `population_thousands`/
`industrial_index` to that region's own aggregate and `province` to its largest
(capital) province as a representative row; a "province" query pins them to that
province's own real, smaller figures. See scenarios/luznova_regional_demand/
scenario.yaml's `ui_extras.levels` for the two bubble sets built from PROVINCES below.

Demand model (per province, per day) — same formula as before, now evaluated at
province granularity so scale correctly differs from a whole region's:
    baseline   = population_thousands * MWH_PER_1000_POP_PER_DAY
    industrial = baseline * (1 + industrial_index / 100 * INDUSTRIAL_MULTIPLIER)
    weather    = industrial * (1 + TEMP_CURVATURE * (mean_temperature_c - MILD_POINT_C) ** 2)
    calendar   = weather * (WEEKEND_FACTOR if is_weekend else 1.0) * (HOLIDAY_FACTOR if is_holiday else 1.0)
    demand_mwh = calendar * lognormal_noise

`mean_temperature_c` reuses each province's *region* climate profile (real per-region
annual-mean temperature/seasonal swing) plus daily noise — province-level climate
nuance isn't the point of this demo.

Run: `python scripts/generate_luznova_regional_demand.py`
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

OUT_PATH = (
    Path(__file__).resolve().parent.parent / "scenarios/luznova_regional_demand/sample_data/luznova_regional_demand.csv"
)
SEED = 42
YEAR = 2024

# Real per-region climate profile (annual-mean temperature °C, seasonal amplitude
# °C) — every province inherits its region's profile. Also carries the region's own
# label, for convenience when building the province table below.
REGION_CLIMATE = {
    "andalucia": {"label": "Andalucía", "avg_temp_c": 18.5, "temp_amplitude_c": 9.0},
    "cataluna": {"label": "Cataluña", "avg_temp_c": 15.5, "temp_amplitude_c": 8.5},
    "madrid": {"label": "Comunidad de Madrid", "avg_temp_c": 14.5, "temp_amplitude_c": 10.5},
    "valenciana": {"label": "Comunitat Valenciana", "avg_temp_c": 17.5, "temp_amplitude_c": 8.0},
    "galicia": {"label": "Galicia", "avg_temp_c": 13.5, "temp_amplitude_c": 5.5},
    "castilla_y_leon": {"label": "Castilla y León", "avg_temp_c": 11.0, "temp_amplitude_c": 10.5},
    "pais_vasco": {"label": "País Vasco", "avg_temp_c": 13.5, "temp_amplitude_c": 7.0},
    "castilla_la_mancha": {"label": "Castilla-La Mancha", "avg_temp_c": 14.0, "temp_amplitude_c": 11.0},
    "canarias": {"label": "Canarias", "avg_temp_c": 20.5, "temp_amplitude_c": 3.5},
    "murcia": {"label": "Región de Murcia", "avg_temp_c": 18.0, "temp_amplitude_c": 8.0},
    "aragon": {"label": "Aragón", "avg_temp_c": 13.5, "temp_amplitude_c": 10.5},
    "baleares": {"label": "Illes Balears", "avg_temp_c": 17.5, "temp_amplitude_c": 7.0},
    "extremadura": {"label": "Extremadura", "avg_temp_c": 16.0, "temp_amplitude_c": 10.5},
    "asturias": {"label": "Asturias", "avg_temp_c": 13.0, "temp_amplitude_c": 5.5},
    "navarra": {"label": "Navarra", "avg_temp_c": 12.5, "temp_amplitude_c": 9.5},
    "cantabria": {"label": "Cantabria", "avg_temp_c": 13.5, "temp_amplitude_c": 5.0},
    "la_rioja": {"label": "La Rioja", "avg_temp_c": 13.0, "temp_amplitude_c": 9.5},
}

# key, label, parent region, real provincial-capital lat/lon, real-approximate 2023
# population (thousands) and a synthetic 0-100 industrial-intensity index — each
# region's provinces' populations sum to ~that region's own real total (see
# REGION_CLIMATE above / the original 17-region figures this replaces).
PROVINCES = [
    # Andalucía (total ~8500)
    {
        "key": "sevilla",
        "label": "Sevilla",
        "region": "andalucia",
        "lat": 37.39,
        "lon": -5.99,
        "population_thousands": 1950,
        "industrial_index": 55,
    },
    {
        "key": "malaga",
        "label": "Málaga",
        "region": "andalucia",
        "lat": 36.72,
        "lon": -4.42,
        "population_thousands": 1700,
        "industrial_index": 40,
    },
    {
        "key": "cadiz",
        "label": "Cádiz",
        "region": "andalucia",
        "lat": 36.53,
        "lon": -6.30,
        "population_thousands": 1240,
        "industrial_index": 50,
    },
    {
        "key": "cordoba",
        "label": "Córdoba",
        "region": "andalucia",
        "lat": 37.89,
        "lon": -4.78,
        "population_thousands": 780,
        "industrial_index": 35,
    },
    {
        "key": "granada",
        "label": "Granada",
        "region": "andalucia",
        "lat": 37.18,
        "lon": -3.60,
        "population_thousands": 915,
        "industrial_index": 30,
    },
    {
        "key": "jaen",
        "label": "Jaén",
        "region": "andalucia",
        "lat": 37.77,
        "lon": -3.79,
        "population_thousands": 610,
        "industrial_index": 25,
    },
    {
        "key": "almeria",
        "label": "Almería",
        "region": "andalucia",
        "lat": 36.84,
        "lon": -2.47,
        "population_thousands": 730,
        "industrial_index": 45,
    },
    {
        "key": "huelva",
        "label": "Huelva",
        "region": "andalucia",
        "lat": 37.26,
        "lon": -6.95,
        "population_thousands": 525,
        "industrial_index": 40,
    },
    # Cataluña (total ~7700)
    {
        "key": "barcelona",
        "label": "Barcelona",
        "region": "cataluna",
        "lat": 41.39,
        "lon": 2.16,
        "population_thousands": 5700,
        "industrial_index": 85,
    },
    {
        "key": "tarragona",
        "label": "Tarragona",
        "region": "cataluna",
        "lat": 41.12,
        "lon": 1.24,
        "population_thousands": 830,
        "industrial_index": 70,
    },
    {
        "key": "girona",
        "label": "Girona",
        "region": "cataluna",
        "lat": 41.98,
        "lon": 2.82,
        "population_thousands": 780,
        "industrial_index": 55,
    },
    {
        "key": "lleida",
        "label": "Lleida",
        "region": "cataluna",
        "lat": 41.62,
        "lon": 0.62,
        "population_thousands": 430,
        "industrial_index": 45,
    },
    # Comunitat Valenciana (total ~5100)
    {
        "key": "valencia",
        "label": "Valencia",
        "region": "valenciana",
        "lat": 39.47,
        "lon": -0.38,
        "population_thousands": 2650,
        "industrial_index": 65,
    },
    {
        "key": "alicante",
        "label": "Alicante",
        "region": "valenciana",
        "lat": 38.35,
        "lon": -0.48,
        "population_thousands": 1950,
        "industrial_index": 55,
    },
    {
        "key": "castellon",
        "label": "Castellón",
        "region": "valenciana",
        "lat": 39.99,
        "lon": -0.04,
        "population_thousands": 580,
        "industrial_index": 60,
    },
    # Galicia (total ~2700)
    {
        "key": "a_coruna",
        "label": "A Coruña",
        "region": "galicia",
        "lat": 43.36,
        "lon": -8.41,
        "population_thousands": 1120,
        "industrial_index": 40,
    },
    {
        "key": "pontevedra",
        "label": "Pontevedra",
        "region": "galicia",
        "lat": 42.43,
        "lon": -8.64,
        "population_thousands": 945,
        "industrial_index": 45,
    },
    {
        "key": "lugo",
        "label": "Lugo",
        "region": "galicia",
        "lat": 43.01,
        "lon": -7.56,
        "population_thousands": 320,
        "industrial_index": 25,
    },
    {
        "key": "ourense",
        "label": "Ourense",
        "region": "galicia",
        "lat": 42.34,
        "lon": -7.86,
        "population_thousands": 305,
        "industrial_index": 25,
    },
    # Castilla y León (total ~2380)
    {
        "key": "valladolid",
        "label": "Valladolid",
        "region": "castilla_y_leon",
        "lat": 41.65,
        "lon": -4.73,
        "population_thousands": 520,
        "industrial_index": 45,
    },
    {
        "key": "leon",
        "label": "León",
        "region": "castilla_y_leon",
        "lat": 42.60,
        "lon": -5.57,
        "population_thousands": 440,
        "industrial_index": 30,
    },
    {
        "key": "burgos",
        "label": "Burgos",
        "region": "castilla_y_leon",
        "lat": 42.34,
        "lon": -3.70,
        "population_thousands": 360,
        "industrial_index": 40,
    },
    {
        "key": "salamanca",
        "label": "Salamanca",
        "region": "castilla_y_leon",
        "lat": 40.97,
        "lon": -5.66,
        "population_thousands": 330,
        "industrial_index": 25,
    },
    {
        "key": "palencia",
        "label": "Palencia",
        "region": "castilla_y_leon",
        "lat": 42.01,
        "lon": -4.53,
        "population_thousands": 155,
        "industrial_index": 30,
    },
    {
        "key": "zamora",
        "label": "Zamora",
        "region": "castilla_y_leon",
        "lat": 41.50,
        "lon": -5.75,
        "population_thousands": 165,
        "industrial_index": 20,
    },
    {
        "key": "avila",
        "label": "Ávila",
        "region": "castilla_y_leon",
        "lat": 40.66,
        "lon": -4.70,
        "population_thousands": 155,
        "industrial_index": 20,
    },
    {
        "key": "segovia",
        "label": "Segovia",
        "region": "castilla_y_leon",
        "lat": 40.95,
        "lon": -4.12,
        "population_thousands": 150,
        "industrial_index": 20,
    },
    {
        "key": "soria",
        "label": "Soria",
        "region": "castilla_y_leon",
        "lat": 41.76,
        "lon": -2.47,
        "population_thousands": 90,
        "industrial_index": 20,
    },
    # País Vasco (total ~2200)
    {
        "key": "vizcaya",
        "label": "Vizcaya",
        "region": "pais_vasco",
        "lat": 43.26,
        "lon": -2.92,
        "population_thousands": 1150,
        "industrial_index": 85,
    },
    {
        "key": "guipuzcoa",
        "label": "Guipúzcoa",
        "region": "pais_vasco",
        "lat": 43.32,
        "lon": -1.98,
        "population_thousands": 715,
        "industrial_index": 80,
    },
    {
        "key": "alava",
        "label": "Álava",
        "region": "pais_vasco",
        "lat": 42.85,
        "lon": -2.67,
        "population_thousands": 335,
        "industrial_index": 70,
    },
    # Castilla-La Mancha (total ~2050)
    {
        "key": "toledo",
        "label": "Toledo",
        "region": "castilla_la_mancha",
        "lat": 39.86,
        "lon": -4.02,
        "population_thousands": 730,
        "industrial_index": 30,
    },
    {
        "key": "ciudad_real",
        "label": "Ciudad Real",
        "region": "castilla_la_mancha",
        "lat": 38.99,
        "lon": -3.93,
        "population_thousands": 485,
        "industrial_index": 25,
    },
    {
        "key": "albacete",
        "label": "Albacete",
        "region": "castilla_la_mancha",
        "lat": 38.99,
        "lon": -1.86,
        "population_thousands": 385,
        "industrial_index": 25,
    },
    {
        "key": "cuenca",
        "label": "Cuenca",
        "region": "castilla_la_mancha",
        "lat": 40.07,
        "lon": -2.14,
        "population_thousands": 195,
        "industrial_index": 15,
    },
    {
        "key": "guadalajara",
        "label": "Guadalajara",
        "region": "castilla_la_mancha",
        "lat": 40.63,
        "lon": -3.17,
        "population_thousands": 255,
        "industrial_index": 30,
    },
    # Canarias (total ~2170)
    {
        "key": "las_palmas",
        "label": "Las Palmas",
        "region": "canarias",
        "lat": 28.10,
        "lon": -15.41,
        "population_thousands": 1150,
        "industrial_index": 20,
    },
    {
        "key": "santa_cruz_tenerife",
        "label": "Santa Cruz de Tenerife",
        "region": "canarias",
        "lat": 28.47,
        "lon": -16.25,
        "population_thousands": 1020,
        "industrial_index": 20,
    },
    # Aragón (total ~1330)
    {
        "key": "zaragoza",
        "label": "Zaragoza",
        "region": "aragon",
        "lat": 41.65,
        "lon": -0.88,
        "population_thousands": 980,
        "industrial_index": 50,
    },
    {
        "key": "huesca",
        "label": "Huesca",
        "region": "aragon",
        "lat": 42.14,
        "lon": -0.41,
        "population_thousands": 220,
        "industrial_index": 30,
    },
    {
        "key": "teruel",
        "label": "Teruel",
        "region": "aragon",
        "lat": 40.34,
        "lon": -1.11,
        "population_thousands": 130,
        "industrial_index": 20,
    },
    # Extremadura (total ~1060)
    {
        "key": "badajoz",
        "label": "Badajoz",
        "region": "extremadura",
        "lat": 38.88,
        "lon": -6.97,
        "population_thousands": 670,
        "industrial_index": 22,
    },
    {
        "key": "caceres",
        "label": "Cáceres",
        "region": "extremadura",
        "lat": 39.48,
        "lon": -6.37,
        "population_thousands": 390,
        "industrial_index": 18,
    },
    # Single-province regions (province == region)
    {
        "key": "asturias",
        "label": "Asturias",
        "region": "asturias",
        "lat": 43.36,
        "lon": -5.85,
        "population_thousands": 1010,
        "industrial_index": 45,
    },
    {
        "key": "baleares",
        "label": "Illes Balears",
        "region": "baleares",
        "lat": 39.57,
        "lon": 2.65,
        "population_thousands": 1200,
        "industrial_index": 15,
    },
    {
        "key": "cantabria",
        "label": "Cantabria",
        "region": "cantabria",
        "lat": 43.46,
        "lon": -3.81,
        "population_thousands": 580,
        "industrial_index": 35,
    },
    {
        "key": "madrid_prov",
        "label": "Madrid",
        "region": "madrid",
        "lat": 40.42,
        "lon": -3.70,
        "population_thousands": 6700,
        "industrial_index": 55,
    },
    {
        "key": "murcia",
        "label": "Murcia",
        "region": "murcia",
        "lat": 37.99,
        "lon": -1.13,
        "population_thousands": 1550,
        "industrial_index": 40,
    },
    {
        "key": "navarra",
        "label": "Navarra",
        "region": "navarra",
        "lat": 42.82,
        "lon": -1.65,
        "population_thousands": 660,
        "industrial_index": 55,
    },
    {
        "key": "la_rioja",
        "label": "La Rioja",
        "region": "la_rioja",
        "lat": 42.47,
        "lon": -2.45,
        "population_thousands": 320,
        "industrial_index": 40,
    },
]

# Each region's largest (capital) province — used by scenario.yaml's ui_extras
# "region" level as the representative `province` value for a whole-region query
# (population_thousands/industrial_index there are overridden to the region's own
# aggregate regardless, so this only needs to be a real, valid province value).
REGION_CAPITAL_PROVINCE = {
    "andalucia": "sevilla",
    "cataluna": "barcelona",
    "valenciana": "valencia",
    "galicia": "a_coruna",
    "castilla_y_leon": "valladolid",
    "pais_vasco": "vizcaya",
    "castilla_la_mancha": "toledo",
    "canarias": "las_palmas",
    "aragon": "zaragoza",
    "extremadura": "badajoz",
    "asturias": "asturias",
    "baleares": "baleares",
    "cantabria": "cantabria",
    "madrid": "madrid_prov",
    "murcia": "murcia",
    "navarra": "navarra",
    "la_rioja": "la_rioja",
}

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
    # Every 2nd day across the full year — still spans the full seasonal range
    # (needed for the temperature "bathtub curve" story) at roughly half the rows
    # of a daily series, keeping the province-level table (50x more rows/day than
    # the old region-level one) at a comparable, ~9-10k row scale to every other
    # scenario in this repo.
    dates = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31", freq="2D")
    rows = []
    for day in dates:
        day_of_year = day.dayofyear
        is_weekend = day.weekday() >= 5
        is_holiday = (day.month, day.day) in NATIONAL_HOLIDAYS
        for prov in PROVINCES:
            climate = REGION_CLIMATE[prov["region"]]
            seasonal = math.cos(2 * math.pi * (day_of_year - 15) / 365.25)
            temp = climate["avg_temp_c"] - climate["temp_amplitude_c"] * seasonal + rng.normal(0, 1.4)

            baseline = prov["population_thousands"] * MWH_PER_1000_POP_PER_DAY
            industrial = baseline * (1 + prov["industrial_index"] / 100 * INDUSTRIAL_MULTIPLIER)
            weather = industrial * (1 + TEMP_CURVATURE * (temp - MILD_POINT_C) ** 2)
            calendar = weather * (WEEKEND_FACTOR if is_weekend else 1.0) * (HOLIDAY_FACTOR if is_holiday else 1.0)
            demand = calendar * math.exp(rng.normal(0, NOISE_SIGMA))

            rows.append(
                {
                    "row_id": f"{day.strftime('%Y%m%d')}_{prov['key']}",
                    "region": prov["region"],
                    "province": prov["key"],
                    "month": day.month,
                    "is_weekend": int(is_weekend),
                    "is_holiday": int(is_holiday),
                    "mean_temperature_c": round(temp, 1),
                    "population_thousands": prov["population_thousands"],
                    "industrial_index": prov["industrial_index"],
                    "demand_mwh": round(demand, 1),
                }
            )

    df = pd.DataFrame(rows)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(df)} rows to {OUT_PATH}")
    print(df["demand_mwh"].describe())
    print(f"provinces: {df['province'].nunique()}, regions: {df['region'].nunique()}")


if __name__ == "__main__":
    main()
