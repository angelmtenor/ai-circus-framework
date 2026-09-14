#!/usr/bin/env python3
"""Synthetic data generator for the `luznova_gas_prospects` scenario.

Produces `scenarios/luznova_gas_prospects/sample_data/luznova_gas_prospects.csv` — one
row per (day, censal_area) across two real Spanish municipios with deliberately
contrasting climate and economics (everything downstream of their real coordinates —
the actual conversion/consumption values — is a designed synthetic formula, not fitted
to any real CRM dataset, same disclosure as `cnc_surface_finish`/`luznova_regional_demand`):

  - Rincón de la Victoria (Málaga, Andalucía) — mild Mediterranean coastal climate,
    4 fictional "censal_area" tracts with different income/building profiles.
  - Miranda de Ebro (Burgos, Castilla y León) — cold continental climate (same regional
    climate profile as `luznova_regional_demand`'s castilla_y_leon, for cross-scenario
    consistency), 4 more tracts.

Two things are modeled per row, both real-world CRM/planning questions for a utility's
sales team deciding where to run a gas-network expansion campaign:

  1. `signed_contract` (the trained target, classification): whether that day's
     marketing contact(s) in that censal_area converted into a signed gas-supply
     contract. Logistic in: network distance (harder/costlier to connect = lower),
     how much cheaper Luznova's offer is than the prospect's current provider,
     marketing touches, referral leads, income, current (non-gas) heating source, and
     a pre-winter seasonal urgency bump.
  2. `potential_daily_consumption_m3` / `potential_daily_revenue_eur` (feature columns,
     NOT the trained target): the household-size/building-type/heating-degree-driven
     gas demand and its euro value if that tract's prospects convert — explorable
     directly (aggregated by month/censal_area, or non-aggregated per day) in ui-react's
     generic Data tab, no bespoke UI.

`municipio` is NOT a trained feature (see scenario.yaml — it's perfectly collinear with
`censal_area`, same reasoning as `luznova_regional_demand` excluding `region` in favor
of `province`); it's kept in the raw CSV only as human-readable provenance.

Run: `python scripts/generate_luznova_gas_prospects.py`
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

OUT_PATH = (
    Path(__file__).resolve().parent.parent / "scenarios/luznova_gas_prospects/sample_data/luznova_gas_prospects.csv"
)
SEED = 7
YEAR = 2024

# Real municipio coordinates + the same per-region climate profile
# (avg_temp_c/temp_amplitude_c) `luznova_regional_demand` uses for andalucia /
# castilla_y_leon, for cross-scenario consistency.
MUNICIPIOS = {
    "rincon_de_la_victoria": {
        "label": "Rincón de la Victoria",
        "lat": 36.7169,
        "lon": -4.2753,
        "avg_temp_c": 18.0,
        "temp_amplitude_c": 7.5,
    },
    "miranda_de_ebro": {
        "label": "Miranda de Ebro",
        "lat": 42.4113,
        "lon": -2.9440,
        "avg_temp_c": 11.0,
        "temp_amplitude_c": 10.5,
    },
}

# Fictional censal_area (census-tract-like) segments — each a distinct economic/
# building profile within its municipio, real-plausible neighborhood names.
CENSAL_AREAS = [
    {
        "key": "rv_centro",
        "label": "Rincón Centro",
        "municipio": "rincon_de_la_victoria",
        "lat": 36.7175,
        "lon": -4.2740,
        "household_income_index": 58,
        "building_type": "apartment",
        "avg_household_size": 2.6,
        "current_heating_source": "electric",
        "distance_to_gas_network_m": 150,
    },
    {
        "key": "rv_torre_benagalbon",
        "label": "Torre de Benagalbón",
        "municipio": "rincon_de_la_victoria",
        "lat": 36.7280,
        "lon": -4.2450,
        "household_income_index": 52,
        "building_type": "apartment",
        "avg_household_size": 2.8,
        "current_heating_source": "butane_lpg",
        "distance_to_gas_network_m": 650,
    },
    {
        "key": "rv_cala_del_moral",
        "label": "La Cala del Moral",
        "municipio": "rincon_de_la_victoria",
        "lat": 36.7300,
        "lon": -4.3150,
        "household_income_index": 72,
        "building_type": "detached_house",
        "avg_household_size": 3.0,
        "current_heating_source": "electric",
        "distance_to_gas_network_m": 900,
    },
    {
        "key": "rv_benagalbon_pueblo",
        "label": "Benagalbón Pueblo",
        "municipio": "rincon_de_la_victoria",
        "lat": 36.7450,
        "lon": -4.2600,
        "household_income_index": 44,
        "building_type": "terraced_house",
        "avg_household_size": 3.2,
        "current_heating_source": "none",
        "distance_to_gas_network_m": 1800,
    },
    {
        "key": "me_casco_historico",
        "label": "Casco Histórico",
        "municipio": "miranda_de_ebro",
        "lat": 42.4090,
        "lon": -2.9470,
        "household_income_index": 48,
        "building_type": "apartment",
        "avg_household_size": 2.4,
        "current_heating_source": "diesel",
        "distance_to_gas_network_m": 120,
    },
    {
        "key": "me_ircio",
        "label": "Ircio",
        "municipio": "miranda_de_ebro",
        "lat": 42.4230,
        "lon": -2.9600,
        "household_income_index": 55,
        "building_type": "apartment",
        "avg_household_size": 2.7,
        "current_heating_source": "electric",
        "distance_to_gas_network_m": 400,
    },
    {
        "key": "me_industrial_norte",
        "label": "Zona Industrial Norte",
        "municipio": "miranda_de_ebro",
        "lat": 42.4260,
        "lon": -2.9300,
        "household_income_index": 40,
        "building_type": "terraced_house",
        "avg_household_size": 3.1,
        "current_heating_source": "diesel",
        "distance_to_gas_network_m": 700,
    },
    {
        "key": "me_bayas",
        "label": "Bayas",
        "municipio": "miranda_de_ebro",
        "lat": 42.3950,
        "lon": -2.9750,
        "household_income_index": 50,
        "building_type": "detached_house",
        "avg_household_size": 3.4,
        "current_heating_source": "butane_lpg",
        "distance_to_gas_network_m": 1600,
    },
]

BUILDING_FACTOR = {"apartment": 0.85, "terraced_house": 1.05, "detached_house": 1.3}
HEATING_SOURCE_LOGIT_EFFECT = {"electric": 0.3, "butane_lpg": 0.5, "diesel": 0.6, "none": -0.4}
HEATING_SOURCE_CONSUMPTION_PENALTY = {"electric": 1.0, "butane_lpg": 1.05, "diesel": 1.05, "none": 1.15}

BASE_M3_PER_PERSON_DAY = 1.1
HEATING_COMFORT_POINT_C = 16.0
HEATING_M3_PER_DEGREE = 0.28
AUTUMN_MONTHS = {9, 10, 11}


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def main() -> None:
    rng = np.random.default_rng(SEED)
    dates = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31", freq="D")
    rows = []

    for day in dates:
        day_of_year = day.dayofyear
        month = day.month

        for area in CENSAL_AREAS:
            climate = MUNICIPIOS[area["municipio"]]
            seasonal = math.cos(2 * math.pi * (day_of_year - 15) / 365.25)
            temp = climate["avg_temp_c"] - climate["temp_amplitude_c"] * seasonal + rng.normal(0, 1.3)

            competitor_price_delta_pct = rng.normal(3.0, 10.0)
            marketing_touches = int(np.clip(rng.poisson(2.2), 0, 8))
            referral_lead = int(rng.random() < 0.08)

            logit = (
                -2.0
                - area["distance_to_gas_network_m"] / 1000 * 1.3
                + competitor_price_delta_pct / 100 * 3.0
                + math.log1p(marketing_touches) * 0.55
                + referral_lead * 0.9
                + (area["household_income_index"] - 50) / 100 * 0.6
                + HEATING_SOURCE_LOGIT_EFFECT[area["current_heating_source"]]
                + (0.35 if month in AUTUMN_MONTHS else 0.0)
                + rng.normal(0, 0.4)
            )
            prob = float(sigmoid(np.array(logit)))
            signed_contract = int(rng.random() < prob)

            heating_deficit = max(0.0, HEATING_COMFORT_POINT_C - temp)
            heating_component = heating_deficit * HEATING_M3_PER_DEGREE * BUILDING_FACTOR[area["building_type"]]
            base_load = BASE_M3_PER_PERSON_DAY * area["avg_household_size"] * 0.5
            consumption = (
                (base_load + heating_component)
                * HEATING_SOURCE_CONSUMPTION_PENALTY[area["current_heating_source"]]
                * math.exp(rng.normal(0, 0.15))
            )
            price_eur_per_m3 = float(np.clip(rng.normal(0.78, 0.03), 0.65, 0.95))
            revenue = consumption * price_eur_per_m3

            rows.append(
                {
                    "row_id": f"{day.strftime('%Y%m%d')}_{area['key']}",
                    "municipio": area["municipio"],
                    "censal_area": area["key"],
                    "month": month,
                    "mean_temperature_c": round(temp, 1),
                    "household_income_index": area["household_income_index"],
                    "building_type": area["building_type"],
                    "avg_household_size": area["avg_household_size"],
                    "current_heating_source": area["current_heating_source"],
                    "distance_to_gas_network_m": area["distance_to_gas_network_m"],
                    "competitor_price_delta_pct": round(competitor_price_delta_pct, 1),
                    "marketing_touches": marketing_touches,
                    "referral_lead": referral_lead,
                    "potential_daily_consumption_m3": round(consumption, 2),
                    "potential_daily_revenue_eur": round(revenue, 2),
                    "signed_contract": signed_contract,
                }
            )

    df = pd.DataFrame(rows)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(df)} rows to {OUT_PATH}")
    print(df["signed_contract"].value_counts(normalize=True))
    print(df[["potential_daily_consumption_m3", "potential_daily_revenue_eur"]].describe())
    print(f"censal_areas: {df['censal_area'].nunique()}, municipios: {df['municipio'].nunique()}")


if __name__ == "__main__":
    main()
