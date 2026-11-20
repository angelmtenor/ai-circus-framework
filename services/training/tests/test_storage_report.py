"""
- Title:    Tests for the storage forecast report and its MLflow mirror
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from training.core import storage_report
from training.core.mlflow_tracking import log_report_run
from training.core.quantile_forecast import QuantileConfig

SCENARIO = Path(__file__).resolve().parents[3] / "scenarios" / "water_reservoirs_drought"


@pytest.mark.skipif(not (SCENARIO / "sample_data").exists(), reason="scenario snapshot not present")
def test_a_short_run_produces_a_consistent_report_and_forecast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_report, "FIRST_TEST_YEAR", 2024)
    config = QuantileConfig(horizons=(7, 30), rounds=15, stride=9, conformal=False)
    out = storage_report.run(SCENARIO, config)
    report, forecast = out["report"], out["forecast"]
    assert report["test_years"][0] == 2024
    assert [r["horizon"] for r in report["overall"]] == [7, 30]
    assert set(report["acceptance"]) == {"p50_beats_both_baselines_at_30", "coverage_within_tolerance"}
    assert report["tracked_in_mlflow"] is False  # no tracker configured
    series = {s["series"] for s in forecast["series"]}
    assert len(series) == 13 and "central" in series
    one = forecast["series"][0]["horizons"]["30"]
    assert one["p10"] <= one["p50"] <= one["p90"]
    assert report["drought_years"] == [2022, 2024] and set(report["drought"]) == {
        "central", "westfield", "north_coast", "south_valley"
    }  # fmt: skip
    text = storage_report.markdown(report)
    assert "Reference implementation on synthetic data" in text and "## Acceptance checks" in text
    assert "drought years 2022-2024" in text
    assert str(report["overall"][0]["mae_model"]) in text  # the markdown reads the same numbers


def test_markdown_lists_the_weak_spots_it_was_given() -> None:
    row = {
        "horizon": 90, "n": 10, "mae_model": 9.1, "mae_persistence": 5.2, "mae_climatology": 30.0,
        "mae_seasonal_drift": 8.0, "coverage": 0.56, "nominal": 0.8,
    }  # fmt: skip
    report: dict[str, Any] = {
        "generated": "2026-01-01", "data_range": ["2000-01-01", "2026-01-01"], "test_years": [2010, 2025],
        "config": {"horizons": [90], "conformal": True}, "cleaning_policy_applied": {"excluded": 1},
        "overall": [row], "systems": [row], "reservoirs": [row], "drought_years": [2022, 2024], "drought": {"s1": [row]},
        "acceptance": {"coverage_within_tolerance": False},
        "drought_weak_spots": ["s1, 90 days: MAE 9.1 pp vs persistence 5.2 pp"],
    }  # fmt: skip
    text = storage_report.markdown(report)
    assert "FAIL: coverage within tolerance" in text
    assert "s1, 90 days: MAE 9.1 pp vs persistence 5.2 pp" in text


def test_mlflow_mirror_is_optional_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    args: dict[str, Any] = {
        "scenario_slug": "s",
        "run_name": "r",
        "params": {"a": 1},
        "metrics": {"m": 1.0},
        "report": {},
    }
    assert log_report_run(None, **args) is False

    calls: list[str] = []

    class Run:
        def __enter__(self) -> None: ...
        def __exit__(self, *exc: object) -> None: ...

    fake = types.SimpleNamespace(
        set_tracking_uri=lambda uri: calls.append(f"uri={uri}"),
        set_experiment=lambda name: calls.append(f"exp={name}"),
        start_run=lambda **kw: Run(),
        log_params=lambda p: calls.append("params"),
        log_metrics=lambda m: calls.append("metrics"),
        log_dict=lambda d, name: calls.append(name),
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    assert log_report_run("http://tracker", **args) is True
    assert calls == ["uri=http://tracker", "exp=scenario/s", "params", "metrics", "report.json"]

    def boom(_: str) -> None:
        raise RuntimeError("tracker down")

    monkeypatch.setattr(fake, "set_tracking_uri", boom)
    assert log_report_run("http://tracker", **args) is False
