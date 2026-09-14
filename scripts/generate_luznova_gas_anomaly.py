#!/usr/bin/env python3
"""Synthetic data generator for the `luznova_gas_anomaly` scenario.

Produces `scenarios/luznova_gas_anomaly/sample_data/luznova_gas_anomaly.csv` — one
row per (fictional) gas meter reading, flagging whether it looks anomalous relative
to that customer's own historical baseline (possible meter drift, tampering, or
other non-technical loss) — mirrors the real "large-scale consumption forecasting &
anomaly detection" utility use case this scenario is modeled on, entirely with
synthetic customers (no real customer data of any kind).

Model:
  - Each customer has a `tariff_type` (residential/commercial/industrial) and a
    `baseline_consumption_m3` (their own expected monthly usage, itself a function of
    tariff type and that month's heating-degree-days — gas usage rises with heating
    demand).
  - A customer is anomalous with probability rising in `meter_age_years` (older
    meters drift more) and somewhat higher for industrial tariffs (bigger equipment,
    more failure modes) — see ANOMALY_LOGIT below.
  - A normal customer's `monthly_consumption_m3` is baseline x small lognormal noise;
    an anomalous one deviates sharply, either under-registering (possible tampering/
    under-metering) or over-registering (drift/leak), roughly evenly split.

Run: `python scripts/generate_luznova_gas_anomaly.py`
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

OUT_PATH = Path(__file__).resolve().parent.parent / "scenarios/luznova_gas_anomaly/sample_data/luznova_gas_anomaly.csv"
SEED = 7
N_ROWS = 8000

TARIFFS = ["residential", "commercial", "industrial"]
TARIFF_WEIGHTS = [0.70, 0.20, 0.10]
# Base monthly consumption (m3) at zero heating-degree-days, per tariff.
TARIFF_BASE_M3 = {"residential": 25.0, "commercial": 180.0, "industrial": 900.0}
# How much heating-degree-days add on top of the tariff base, per tariff.
TARIFF_HDD_SENSITIVITY = {"residential": 0.55, "commercial": 2.4, "industrial": 6.5}


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def main() -> None:
    rng = np.random.default_rng(SEED)

    tariff_type = rng.choice(TARIFFS, size=N_ROWS, p=TARIFF_WEIGHTS)
    building_age_years = rng.uniform(0, 80, size=N_ROWS).round(1)
    # Meter age correlates loosely with building age (older buildings *tend* to have
    # older meters) but with enough independent spread that a meter can have been
    # replaced recently in an old building, or vice versa.
    meter_age_years = np.clip(building_age_years * 0.25 + rng.normal(6, 5, size=N_ROWS), 0, 30).round(1)
    heating_degree_days = np.clip(rng.normal(220, 130, size=N_ROWS), 0, 480).round(0)

    baseline_consumption_m3 = np.array(
        [
            TARIFF_BASE_M3[t] + TARIFF_HDD_SENSITIVITY[t] * hdd + rng.normal(0, TARIFF_BASE_M3[t] * 0.08)
            for t, hdd in zip(tariff_type, heating_degree_days, strict=True)
        ]
    )
    baseline_consumption_m3 = np.clip(baseline_consumption_m3, 5.0, None).round(1)

    is_industrial = tariff_type == "industrial"
    anomaly_logit = -4.2 + 0.075 * meter_age_years + 0.5 * is_industrial.astype(float)
    p_anomaly = 1.0 / (1.0 + np.exp(-anomaly_logit))
    is_anomaly = rng.binomial(1, p_anomaly)

    monthly_consumption_m3 = np.empty(N_ROWS)
    for i in range(N_ROWS):
        if is_anomaly[i] == 1:
            # Roughly even split between under- and over-registering anomalies.
            if rng.random() < 0.5:
                factor = rng.uniform(0.25, 0.60)  # possible tampering / under-metering
            else:
                factor = rng.uniform(1.55, 2.6)  # drift / leak
        else:
            factor = math.exp(rng.normal(0, 0.07))
        monthly_consumption_m3[i] = baseline_consumption_m3[i] * factor

    df = pd.DataFrame(
        {
            "meter_id": [f"LZ-GAS-{i:06d}" for i in range(N_ROWS)],
            "tariff_type": tariff_type,
            "monthly_consumption_m3": monthly_consumption_m3.round(1),
            "baseline_consumption_m3": baseline_consumption_m3,
            "heating_degree_days": heating_degree_days,
            "building_age_years": building_age_years,
            "meter_age_years": meter_age_years,
            "is_anomaly": is_anomaly,
        }
    )
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(df)} rows to {OUT_PATH}")
    print(df["is_anomaly"].value_counts(normalize=True))


if __name__ == "__main__":
    main()
