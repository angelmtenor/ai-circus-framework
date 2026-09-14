#!/usr/bin/env python3
"""Synthetic data generator for the `luznova_ev_charging` scenario.

Produces `scenarios/luznova_ev_charging/sample_data/luznova_ev_charging.csv` — one
row per (fictional) EV charging session, mirroring the real "large-scale EV charging
demand & duration prediction" utility use case: a deliberately small set of noisy
behavioral features (session start hour, initial state of charge, charger power
tier, vehicle battery capacity, station region, weekend flag) rather than a direct
physics readout, since a real deployment only ever observes behavioral session data,
not the underlying charge curve.

Model (session duration is NOT a feature — it's an unobserved factor, same as real
charging behavior the utility never directly measures):
    session_duration_hours ~ lognormal(around a typical duration for this charger tier)
    energy_needed_to_full   = vehicle_battery_kwh * (1 - initial_soc_pct / 100) * CHARGE_EFFICIENCY
    max_deliverable         = charger_power_kw * session_duration_hours * CHARGE_EFFICIENCY
    energy_charged_kwh      = min(energy_needed_to_full, max_deliverable) * lognormal_noise

`station_region` is included as real operational metadata (which city's network the
session belongs to) but carries no built-in causal effect on energy delivered — same
"noisy, not every feature is strongly predictive" character the real use case
describes, and the same honest treatment `turbofan_rul` gives its near-constant
operating settings.

Run: `python scripts/generate_luznova_ev_charging.py`
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUT_PATH = Path(__file__).resolve().parent.parent / "scenarios/luznova_ev_charging/sample_data/luznova_ev_charging.csv"
SEED = 13
N_ROWS = 9000

CHARGER_TYPES = ["slow_7kw", "fast_22kw", "rapid_50kw", "ultra_150kw"]
CHARGER_WEIGHTS = [0.35, 0.35, 0.20, 0.10]
CHARGER_POWER_KW = {"slow_7kw": 7.0, "fast_22kw": 22.0, "rapid_50kw": 50.0, "ultra_150kw": 150.0}
CHARGER_TYPICAL_DURATION_H = {"slow_7kw": 6.0, "fast_22kw": 2.0, "rapid_50kw": 0.6, "ultra_150kw": 0.3}

REGIONS = ["madrid", "barcelona", "valencia", "sevilla", "bilbao", "zaragoza", "malaga", "murcia"]
CHARGE_EFFICIENCY = 0.92


def main() -> None:
    rng = np.random.default_rng(SEED)

    charger_type = rng.choice(CHARGER_TYPES, size=N_ROWS, p=CHARGER_WEIGHTS)
    station_region = rng.choice(REGIONS, size=N_ROWS)
    session_start_hour = rng.integers(0, 24, size=N_ROWS)
    is_weekend = rng.binomial(1, 0.28, size=N_ROWS)  # ~2/7 of sessions
    initial_soc_pct = np.clip(rng.normal(38, 18, size=N_ROWS), 2, 90).round(1)
    vehicle_battery_kwh = np.clip(rng.normal(62, 16, size=N_ROWS), 28, 110).round(1)

    typical_duration = np.array([CHARGER_TYPICAL_DURATION_H[c] for c in charger_type])
    # Overnight (slow) sessions run a bit longer on weekends; fast/rapid sessions are
    # largely weekday commute/errand top-ups and don't shift much.
    weekend_stretch = np.where((charger_type == "slow_7kw") & (is_weekend == 1), 1.15, 1.0)
    session_duration_hours = typical_duration * weekend_stretch * np.exp(rng.normal(0, 0.35, size=N_ROWS))

    power_kw = np.array([CHARGER_POWER_KW[c] for c in charger_type])
    energy_needed_to_full = vehicle_battery_kwh * (1 - initial_soc_pct / 100) * CHARGE_EFFICIENCY
    max_deliverable = power_kw * session_duration_hours * CHARGE_EFFICIENCY
    energy_charged_kwh = np.minimum(energy_needed_to_full, max_deliverable) * np.exp(rng.normal(0, 0.06, size=N_ROWS))
    energy_charged_kwh = np.clip(energy_charged_kwh, 0.5, None).round(2)

    df = pd.DataFrame(
        {
            "session_id": [f"LZ-EV-{i:06d}" for i in range(N_ROWS)],
            "session_start_hour": session_start_hour,
            "initial_soc_pct": initial_soc_pct,
            "charger_type": charger_type,
            "vehicle_battery_kwh": vehicle_battery_kwh,
            "station_region": station_region,
            "is_weekend": is_weekend,
            "energy_charged_kwh": energy_charged_kwh,
        }
    )
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(df)} rows to {OUT_PATH}")
    print(df["energy_charged_kwh"].describe())


if __name__ == "__main__":
    main()
