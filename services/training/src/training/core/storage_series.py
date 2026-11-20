"""
- Title:    Storage series: cleaned daily percentages per reservoir and per supply system, with catchment drivers
- Author:   Angel Martinez-Tenor

The input of `quantile_forecast` for the reservoirs scenario: the rain and temperature of each series' catchment
sit next to its storage percentage.

Everything is config-driven (the scenario's `config/*.yaml` and the committed snapshot): another basin with the
same file layout needs no code. Pure pandas, no I/O beyond reading the snapshot directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from ai_circus_shared.geo import haversine_km
from ai_circus_shared.open_data import Observation
from ai_circus_shared.series_quality import QualityConfig, check, cleaned

from training.core.quantile_forecast import Frame

SERIES_COLUMNS = ["series", "kind", "day", "y", "precip_mm", "temp_c"]


def load_snapshot(scenario_dir: Path) -> tuple[Frame, Frame, dict, dict, QualityConfig]:
    """The committed snapshot: readings, weather, reservoir metadata, data-source config, quality config."""
    sample = scenario_dir / "sample_data"
    readings = pd.read_parquet(sample / "reservoir_readings.parquet")
    weather = pd.read_parquet(sample / "weather_daily.parquet")
    meta = json.loads((sample / "reservoirs.json").read_text())
    config = yaml.safe_load((scenario_dir / "config" / "data_sources.yaml").read_text())
    quality = QualityConfig(**yaml.safe_load((scenario_dir / "config" / "quality.yaml").read_text()))
    return readings, weather, meta, config, quality


def clean_readings(readings: Frame, quality: QualityConfig) -> tuple[Frame, dict[str, int]]:
    """The training copy of the readings (series_quality.cleaned's documented policy) as a daily table."""

    def num(value: float) -> float | None:
        return None if pd.isna(value) else float(value)

    observations = [
        Observation(
            series=r.reservoir, day=r.day.date(), level=num(r.level_m), pct=num(r.pct), volume=num(r.volume_hm3)
        )
        for r in readings.itertuples()
    ]
    kept, notes = cleaned(observations, check(observations, quality), quality)
    df = pd.DataFrame([o.model_dump() for o in kept]).rename(columns={"series": "reservoir", "volume": "volume_hm3"})
    df["day"] = pd.to_datetime(df["day"])
    return df.sort_values(["reservoir", "day"]).reset_index(drop=True), notes


def _catchment_drivers(weather: Frame, meta: dict, catchment: dict) -> dict[str, Frame]:
    """Daily mean precipitation and temperature of the stations around each reservoir."""
    stations = weather.drop_duplicates("station").set_index("station")[["lat", "lon"]]
    out: dict[str, Frame] = {}
    for rid, m in meta.items():
        dist = sorted((haversine_km(m["lat"], m["lon"], s.lat, s.lon), code) for code, s in stations.iterrows())
        near = [c for d, c in dist if d <= catchment["radius_km"]]
        if len(near) < catchment["min_stations"]:
            near = [c for d, c in dist if d <= catchment["max_radius_km"]][: catchment["min_stations"]]
        sub = weather[weather["station"].isin(near)]
        out[rid] = sub.groupby("day")[["precip_mm", "temp_c"]].mean()
    return out


def build_series(scenario_dir: Path) -> tuple[Frame, dict]:
    """One long table: `series`, `kind` (reservoir|system), `day`, `y` (storage %), and the catchment drivers.

    A system's storage is capacity-weighted (sum of volumes over sum of capacities, on days when every member
    has a reading); its drivers are the capacity-weighted mean of its members'. Returns the table and a dict of
    what the cleaning policy did, so a report can state it.
    """
    readings, weather, meta, config, quality = load_snapshot(scenario_dir)
    systems = yaml.safe_load((scenario_dir / "config" / "systems.yaml").read_text())["systems"]
    clash = set(meta) & {s["id"] for s in systems}
    if clash:
        raise ValueError(f"a system id equals a reservoir id: {sorted(clash)}")
    clean, notes = clean_readings(readings, quality)
    days = pd.date_range(clean["day"].min(), clean["day"].max(), freq="D")
    drivers = _catchment_drivers(weather, meta, config["weather"]["catchment"])

    vol = clean.pivot(index="day", columns="reservoir", values="volume_hm3").reindex(days)
    pct = clean.pivot(index="day", columns="reservoir", values="pct").reindex(days)
    cap = {rid: m["capacity_hm3"] for rid, m in meta.items()}
    frames: list[Frame] = []

    def add(name: str, kind: str, y: Frame, precip: Frame, temp: Frame) -> None:
        frames.append(
            pd.DataFrame({
                "series": name,
                "kind": kind,
                "day": days,
                "y": y.values,
                "precip_mm": precip.values,
                "temp_c": temp.values,
            })
        )

    for rid in meta:
        d = drivers[rid].reindex(days)
        add(rid, "reservoir", pct[rid], d["precip_mm"], d["temp_c"])
    for s in systems:
        members = [r for r in s["reservoirs"] if r in meta]
        total = sum(cap[r] for r in members)
        y = vol[members].sum(axis=1, min_count=len(members)) / total * 100
        weights = np.array([cap[r] / total for r in members])
        precip = sum(drivers[r].reindex(days)["precip_mm"].fillna(0) * w for r, w in zip(members, weights, strict=True))
        temp = sum(drivers[r].reindex(days)["temp_c"].ffill() * w for r, w in zip(members, weights, strict=True))
        add(s["id"], "system", y, precip, temp)
    table = pd.concat(frames, ignore_index=True)[SERIES_COLUMNS]
    return table, {"cleaning": notes, "capacity_hm3": cap, "systems": {s["id"]: s["reservoirs"] for s in systems}}
