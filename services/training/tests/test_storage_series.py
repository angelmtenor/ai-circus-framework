"""
- Title:    Tests for the reservoirs scenario's storage series (built from the committed snapshot)
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from training.core import storage_series

SCENARIO = Path(__file__).resolve().parents[3] / "scenarios" / "water_reservoirs_drought"

pytestmark = pytest.mark.skipif(not (SCENARIO / "sample_data").exists(), reason="scenario snapshot not present")


@pytest.fixture(scope="module")
def built() -> tuple[pd.DataFrame, dict]:
    return storage_series.build_series(SCENARIO)


def test_nine_reservoirs_and_four_systems(built: tuple[pd.DataFrame, dict]) -> None:
    table, notes = built
    kinds = table.drop_duplicates("series").set_index("series")["kind"]
    assert (kinds == "reservoir").sum() == 9 and (kinds == "system").sum() == 4
    assert table.duplicated(["series", "day"]).sum() == 0
    assert notes["cleaning"]["excluded"] > 0  # the jump policy ran


def test_system_storage_is_capacity_weighted(built: tuple[pd.DataFrame, dict]) -> None:
    table, notes = built
    readings, _, _, _, quality = storage_series.load_snapshot(SCENARIO)
    clean, _ = storage_series.clean_readings(readings, quality)
    day = pd.Timestamp("2023-03-01")
    members = notes["systems"]["central"]
    volume = clean[(clean["day"] == day) & clean["reservoir"].isin(members)]["volume_hm3"].sum()
    capacity = sum(notes["capacity_hm3"][m] for m in members)
    got = table[(table["series"] == "central") & (table["day"] == day)]["y"].iloc[0]
    assert got == pytest.approx(volume / capacity * 100)


def test_catchment_drivers_are_present_for_every_series(built: tuple[pd.DataFrame, dict]) -> None:
    table, _ = built
    share = table.groupby("series")["precip_mm"].apply(lambda s: s.notna().mean())
    assert (share > 0.95).all()


def test_a_system_id_equal_to_a_reservoir_id_is_refused(tmp_path: Path) -> None:
    import shutil

    shutil.copytree(SCENARIO, tmp_path / "s", ignore=shutil.ignore_patterns("backtest*", "forecast.json"))
    systems = tmp_path / "s" / "config" / "systems.yaml"
    systems.write_text(systems.read_text().replace("id: westfield", "id: lantern_lake"))
    with pytest.raises(ValueError, match="system id equals a reservoir id"):
        storage_series.build_series(tmp_path / "s")
