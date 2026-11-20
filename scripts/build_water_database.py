#!/usr/bin/env python3
"""Build the small SQLite file the assistant's SQL tool reads, from the committed snapshot.

    PYTHONPATH=services/training/src:libs/shared/src services/training/.venv/bin/python scripts/build_water_database.py

Tables (all synthetic data, identical for every tenant): systems, reservoirs, readings (as published, with the
duplicates and out-of-range values that need review), storage_daily (the cleaned percentage per reservoir and per
system, the series the forecast uses) and dq_flags. The assistant opens it read-only through
ai_circus_shared.readonly_sql, which allows only these tables.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import yaml
from training.core.storage_series import build_series

SCENARIO = Path(__file__).resolve().parent.parent / "scenarios" / "water_reservoirs_drought"
OUT = SCENARIO / "sample_data" / "water.sqlite.gz"

SCHEMA = """
CREATE TABLE systems (id TEXT PRIMARY KEY, name TEXT, capacity_hm3 REAL);
CREATE TABLE reservoirs (id TEXT PRIMARY KEY, name TEXT, system_id TEXT, capacity_hm3 REAL, lat REAL, lon REAL,
                         basin TEXT, dq_score REAL);
CREATE TABLE readings (reservoir TEXT, day TEXT, level_m REAL, pct REAL, volume_hm3 REAL);
CREATE TABLE storage_daily (series TEXT, kind TEXT, day TEXT, pct REAL);
CREATE TABLE dq_flags (reservoir TEXT, day TEXT, code TEXT, detail TEXT);
CREATE INDEX readings_ix ON readings (reservoir, day);
CREATE INDEX storage_ix ON storage_daily (series, day);
CREATE INDEX flags_ix ON dq_flags (reservoir, day);
"""


def _rows(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    out = df[columns].astype(object).where(df[columns].notna(), None)
    return [tuple(r) for r in out.itertuples(index=False, name=None)]


def main() -> None:
    sample = SCENARIO / "sample_data"
    meta = json.loads((sample / "reservoirs.json").read_text())
    systems = yaml.safe_load((SCENARIO / "config" / "systems.yaml").read_text())["systems"]
    readings = pd.read_parquet(sample / "reservoir_readings.parquet")
    flags = pd.read_parquet(sample / "dq_flags.parquet")
    table, _ = build_series(SCENARIO)
    table = table[table["y"].notna()]

    work = Path(tempfile.mkdtemp()) / "water.sqlite"
    con = sqlite3.connect(work)
    con.executescript(SCHEMA)
    caps = {s["id"]: sum(meta[r]["capacity_hm3"] for r in s["reservoirs"]) for s in systems}
    con.executemany(
        "INSERT INTO systems VALUES (?, ?, ?)", [(s["id"], s["name"], round(caps[s["id"]], 3)) for s in systems]
    )
    con.executemany(
        "INSERT INTO reservoirs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (rid, m["name"], m["system_id"], m["capacity_hm3"], m["lat"], m["lon"], m["basin"], m["dq_score"])
            for rid, m in meta.items()
        ],
    )
    readings = readings.assign(day=readings["day"].dt.strftime("%Y-%m-%d"))
    con.executemany(
        "INSERT INTO readings VALUES (?, ?, ?, ?, ?)",
        _rows(readings, ["reservoir", "day", "level_m", "pct", "volume_hm3"]),
    )
    table = table.assign(day=table["day"].dt.strftime("%Y-%m-%d"), pct=table["y"].round(2))
    con.executemany("INSERT INTO storage_daily VALUES (?, ?, ?, ?)", _rows(table, ["series", "kind", "day", "pct"]))
    flags = flags.assign(day=flags["day"].dt.strftime("%Y-%m-%d"))
    con.executemany("INSERT INTO dq_flags VALUES (?, ?, ?, ?)", _rows(flags, ["reservoir", "day", "code", "detail"]))
    con.commit()
    con.execute("VACUUM")
    for name in ("systems", "reservoirs", "readings", "storage_daily", "dq_flags"):
        print(name, con.execute(f"SELECT count(*) FROM {name}").fetchone()[0])  # noqa: S608 - fixed names
    con.close()
    # Committed compressed (the assistant unpacks it once); mtime 0 and no name keep the bytes reproducible.
    with work.open("rb") as raw, OUT.open("wb") as out, gzip.GzipFile("", "wb", 9, out, 0) as packed:
        shutil.copyfileobj(raw, packed)
    print(f"{OUT} {OUT.stat().st_size / 1e6:.1f} MB (unpacked {work.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
