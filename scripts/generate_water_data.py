#!/usr/bin/env python3
"""
- Title:    Generate the calibrated synthetic snapshot of the Reservoirs & Drought Watch scenario
- Author:   Angel Martinez-Tenor

The fictional Riverbend basin: weather stations, nine reservoirs and their daily readings, simulated with a simple
water balance so the series behave like real storage (dry summers, wet autumns, multi-year droughts, slow recovery,
operating rules that cut releases as storage falls). The climate and each reservoir's long-run mean and turnover are
calibration targets in config/ (data_sources.yaml `generator`, systems.yaml); nothing here names or reads a real place.

The readings are then "published" the way an unvalidated feed is: duplicate days, short gaps, frozen sensors, single
spikes, readings just above 100 % and a stretch with a shifted level-to-volume table, so the data-quality view has
something real to flag. Seeded: the same config gives the same files.

    PYTHONPATH=libs/shared/src services/training/.venv/bin/python scripts/generate_water_data.py [--check]

Writes sample_data/reservoir_readings.parquet, weather_daily.parquet, dq_flags.parquet, reservoirs.json and
SNAPSHOT.json (row counts, SHA-256 of each file). `--check` only verifies the committed files against the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ai_circus_shared.geo import haversine_km
from ai_circus_shared.open_data import Observation
from ai_circus_shared.series_quality import QualityConfig, check

SCENARIO = Path(__file__).resolve().parent.parent / "scenarios" / "water_reservoirs_drought"
OUT = SCENARIO / "sample_data"
FILES = ["reservoir_readings.parquet", "weather_daily.parquet", "dq_flags.parquet", "reservoirs.json"]
KM_PER_DEG = 111.195  # the grid is written as degrees around (0, 0) so the shared haversine works on it unchanged


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_snapshot() -> int:
    manifest = json.loads((OUT / "SNAPSHOT.json").read_text())
    bad = [name for name, digest in manifest["sha256"].items() if _sha(OUT / name) != digest]
    print("snapshot matches its manifest" if not bad else f"snapshot differs from its manifest: {bad}")
    return 1 if bad else 0


def _monthly(values: list[float], days: pd.DatetimeIndex) -> np.ndarray:
    """A monthly climatology interpolated smoothly to every day (values sit mid-month)."""
    doy = days.dayofyear.to_numpy()
    mids = np.array([15 + 30.44 * m for m in range(12)])
    xs = np.concatenate([mids - 365.25, mids, mids + 365.25])
    return np.interp(doy, xs, np.tile(values, 3))


def _weather(gen: dict, days: pd.DatetimeIndex, rng: np.random.Generator) -> tuple[pd.DataFrame, np.ndarray]:
    """Daily precipitation and temperature per station: one regional weather pattern, local variation on top."""
    width, height = gen["grid_km"]
    n = gen["stations"]
    xy = np.column_stack([rng.uniform(4, width - 10, n), rng.uniform(12, height - 4, n)])
    wet_frac = _monthly(gen["wet_day_frac"], days)
    wet_mm = _monthly(gen["wet_day_mm"], days)
    clim_t = _monthly(gen["temp_c"], days)
    years = days.year.to_numpy()
    factor = {y: float(np.exp(rng.normal(0, 0.08))) for y in np.unique(years)}
    factor.update({int(y): float(f) for y, f in gen["year_rain_factor"].items()})
    year_f = np.array([factor[y] for y in years])
    # Regional wet days: a two-state chain, so rain and dry spells cluster. A dry year has fewer and lighter storms.
    regional = np.zeros(len(days), dtype=bool)
    for i in range(1, len(days)):
        p = wet_frac[i] * (2.0 if regional[i - 1] else 0.85) * year_f[i] ** 0.5
        regional[i] = rng.random() < min(p, 0.9)
    storm = rng.gamma(0.75, 1 / 0.75, len(days)) * wet_mm * year_f**0.5
    t_anom = np.zeros(len(days))
    for i in range(1, len(days)):
        t_anom[i] = 0.8 * t_anom[i - 1] + rng.normal(0, 1.1)
    trend = (days.year.to_numpy() - 2000) * 0.03
    frames = []
    for k, (x, y) in enumerate(xy):
        relief = y / height  # the north of the basin is the high, wet ground
        local_wet = np.where(regional, rng.random(len(days)) < 0.82, rng.random(len(days)) < 0.03)
        precip = np.where(local_wet, storm * (0.75 + 0.6 * relief) * rng.lognormal(0, 0.35, len(days)), 0.0)
        precip = np.where(local_wet & ~regional, rng.gamma(0.6, 3.0, len(days)), precip)
        temp = clim_t + t_anom + trend - 4.0 * (relief - 0.5) + rng.normal(0, 0.8, len(days))
        df = pd.DataFrame({
            "station": f"WS{k + 1:02d}",
            "day": days,
            "precip_mm": np.round(precip, 1),
            "temp_c": np.round(temp - 2.0 * (~regional & (precip > 0)), 1),
            "lat": round(y / KM_PER_DEG, 5),
            "lon": round(x / KM_PER_DEG, 5),
        })
        lost = rng.random(len(days)) < 0.004  # a station misses a day now and then
        df.loc[lost, ["precip_mm", "temp_c"]] = np.nan
        frames.append(df)
    return pd.concat(frames, ignore_index=True), xy


def _catchment(weather: pd.DataFrame, xy: np.ndarray, res: dict, catchment: dict) -> pd.DataFrame:
    dist = sorted((math.hypot(res["x_km"] - x, res["y_km"] - y), f"WS{k + 1:02d}") for k, (x, y) in enumerate(xy))
    near = [c for d, c in dist if d <= catchment["radius_km"]]
    if len(near) < catchment["min_stations"]:
        near = [c for d, c in dist if d <= catchment["max_radius_km"]][: catchment["min_stations"]]
    return weather[weather["station"].isin(near)].groupby("day")[["precip_mm", "temp_c"]].mean()


def _runoff(precip: np.ndarray, temp: np.ndarray) -> np.ndarray:
    """mm/day leaving the catchment: a soil bucket that fills with rain and dries with warmth."""
    soil_max, soil = 140.0, 60.0
    out = np.empty(len(precip))
    for i, (p, t) in enumerate(zip(precip, temp, strict=True)):
        soil += p
        et = 0.16 * max(t, 0.0) * soil / soil_max
        quick = 0.3 * max(soil - 0.65 * soil_max, 0.0)
        base = 0.006 * soil
        soil = max(soil - et - quick - base, 0.0)
        out[i] = quick + base
    return out


def _restriction(pct: float) -> float:
    """The operator's response: releases fall as storage falls (the rulebook's states, simplified)."""
    if pct >= 60:
        return 1.0
    if pct >= 40:
        return 0.85
    if pct >= 25:
        return 0.65
    if pct >= 15:
        return 0.45
    return 0.3


def _simulate(inflow: np.ndarray, temp: np.ndarray, doy: np.ndarray, cap: float, demand: float) -> np.ndarray:
    """Daily storage in % of capacity; above 100 % the reservoir spills (a little surcharge is allowed)."""
    season = 1 + 0.4 * np.sin(2 * np.pi * (doy - 110) / 365.25)
    s = 0.7 * cap
    out = np.empty(len(inflow))
    for i in range(len(inflow)):
        pct = 100 * s / cap
        evap = 0.0004 * cap * (pct / 100) * max(temp[i], 0.0) / 20
        release = demand * season[i] * _restriction(pct)
        s = s + inflow[i] - evap - min(release, max(s - 0.005 * cap, 0.0))
        if s > cap:  # the spillway passes the surplus the same day; a flood leaves a brief surcharge
            s = cap + min(0.02 * (s - cap), 0.006 * cap)
        out[i] = 100 * max(s, 0.0) / cap
    return out


def _calibrate(inflow: np.ndarray, temp: np.ndarray, doy: np.ndarray, cap: float, target: float) -> np.ndarray:
    """The base release that gives the target long-run mean storage (bisection: storage falls as demand grows)."""
    lo, hi = 0.0, float(inflow.mean()) * 3
    for _ in range(28):
        mid = (lo + hi) / 2
        if _simulate(inflow, temp, doy, cap, mid).mean() > target:
            lo = mid
        else:
            hi = mid
    return _simulate(inflow, temp, doy, cap, (lo + hi) / 2)


def _publish(rid: str, days: pd.DatetimeIndex, pct: np.ndarray, cap: float, rng: np.random.Generator) -> pd.DataFrame:
    """The readings as an unvalidated feed would publish them, with the faults a data-quality check must catch."""
    head = rng.uniform(25, 60)  # dam height above the lowest level, m
    base = rng.uniform(150, 800)  # m above sea level
    level = base + head * np.clip(pct / 100, 0, 1.05) ** 0.45
    df = pd.DataFrame({"reservoir": rid, "day": days, "pct": pct, "level_m": level})
    df["volume_hm3"] = df["pct"] * cap / 100
    n = len(df)
    # a revised level-to-volume table: one stretch where the level reads ~0.6 m high
    start = int(rng.integers(n // 4, n - 400))
    df.loc[start : start + int(rng.integers(90, 240)), "level_m"] += rng.uniform(0.5, 0.9)
    # frozen sensors: the same reading repeated for weeks
    cols = ["pct", "level_m", "volume_hm3"]
    for _ in range(int(rng.integers(1, 4))):
        a = int(rng.integers(0, n - 60))
        df.loc[a : a + int(rng.integers(16, 40)), cols] = df.loc[a, cols].to_numpy()
    # single spikes
    spikes = rng.choice(n, int(rng.integers(4, 14)), replace=False)
    df.loc[spikes, "pct"] += rng.choice([-1, 1], len(spikes)) * rng.uniform(7, 15, len(spikes))
    df.loc[spikes, "volume_hm3"] = df.loc[spikes, "pct"] * cap / 100
    df["pct"] = df["pct"].round(1)
    df["volume_hm3"] = df["volume_hm3"].round(3 if cap < 10 else 2)
    df["level_m"] = df["level_m"].round(2)
    # gaps, then the same day published twice
    keep = np.ones(n, dtype=bool)
    for _ in range(int(rng.integers(1, 4))):
        a = int(rng.integers(0, n - 10))
        keep[a : a + int(rng.integers(1, 7))] = False
    df = df[keep]
    dupes = df.sample(frac=0.002, random_state=int(rng.integers(1 << 31)))
    return pd.concat([df, dupes]).sort_values("day", kind="stable").reset_index(drop=True)


def generate() -> None:
    sources = yaml.safe_load((SCENARIO / "config" / "data_sources.yaml").read_text())
    systems = yaml.safe_load((SCENARIO / "config" / "systems.yaml").read_text())
    quality = QualityConfig(**yaml.safe_load((SCENARIO / "config" / "quality.yaml").read_text()))
    gen = sources["generator"]
    rng = np.random.default_rng(gen["seed"])
    days = pd.date_range(gen["start"], gen["end"], freq="D")
    doy = days.dayofyear.to_numpy()

    weather, xy = _weather(gen, days, rng)
    catchment = sources["weather"]["catchment"]
    readings = []
    for rid, res in systems["reservoirs"].items():
        drivers = _catchment(weather, xy, res, catchment).reindex(days)
        precip = drivers["precip_mm"].fillna(0).to_numpy()
        temp = drivers["temp_c"].ffill().bfill().to_numpy()
        runoff = _runoff(precip, temp)
        cap = float(res["capacity_hm3"])
        # catchment area so the yearly inflow is `turnover` times the capacity
        area_km2 = res["turnover"] * cap * 1000 / (runoff.mean() * 365.25)
        inflow = runoff * area_km2 / 1000
        pct = _calibrate(inflow, temp, doy, cap, res["target_mean_pct"])
        readings.append(_publish(rid, days, pct, cap, rng))
    readings_df = pd.concat(readings, ignore_index=True)

    report = check(
        (
            Observation(series=r.reservoir, day=r.day.date(), level=r.level_m, pct=r.pct, volume=r.volume_hm3)
            for r in readings_df.itertuples()
        ),
        quality,
    )
    flags = pd.DataFrame([f.model_dump() for f in report.flags], columns=["series", "day", "code", "detail"]).rename(
        columns={"series": "reservoir"}
    )
    flags["day"] = pd.to_datetime(flags["day"])

    system_of = {rid: s["id"] for s in systems["systems"] for rid in s["reservoirs"]}
    meta = {
        rid: {
            "name": r["name"],
            "system_id": system_of[rid],
            "capacity_hm3": r["capacity_hm3"],
            "station_code": f"R{i + 1:02d}",
            "x_km": r["x_km"],
            "y_km": r["y_km"],
            "lat": round(r["y_km"] / KM_PER_DEG, 5),
            "lon": round(r["x_km"] / KM_PER_DEG, 5),
            "basin": f"{r['river']} river",
            "subbasin": f"{r['river']} river",
            "station_name": f"{r['name']} dam",
            "dq_score": report.scores[rid],
        }
        for i, (rid, r) in enumerate(systems["reservoirs"].items())
    }
    # the generator and downstream measure distance the same way
    assert abs(haversine_km(0, 0, 0, 1) - KM_PER_DEG) < 0.1

    OUT.mkdir(parents=True, exist_ok=True)
    readings_df.to_parquet(OUT / "reservoir_readings.parquet", index=False)
    weather.to_parquet(OUT / "weather_daily.parquet", index=False)
    flags.to_parquet(OUT / "dq_flags.parquet", index=False)
    (OUT / "reservoirs.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    manifest = {
        "generated": gen["end"],
        "seed": gen["seed"],
        "rows": {"reservoir_readings": len(readings_df), "weather_daily": len(weather), "dq_flags": len(flags)},
        "range": [str(days.min().date()), str(days.max().date())],
        "dq_counts": report.counts(),
        "attribution": sources["attribution"],
        "data_provenance": sources["data_provenance"],
        "sha256": {name: _sha(OUT / name) for name in FILES},
    }
    (OUT / "SNAPSHOT.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: manifest[k] for k in ("rows", "range", "dq_counts")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate the synthetic Riverbend snapshot.")
    parser.add_argument("--check", action="store_true", help="verify the committed snapshot against its manifest")
    sys.exit(check_snapshot() if parser.parse_args().check else (generate() or 0))
