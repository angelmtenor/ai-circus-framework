"""
- Title:    Tests for the `lightgbm_quantile` candidate in ForecastRun's shape
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from ai_circus_shared.scenario_schema import ScenarioDefinition

from training.core.quantile_pipeline import daily_path, run_quantile_forecast

SCENARIO = Path(__file__).resolve().parents[3] / "scenarios" / "water_reservoirs_drought" / "scenario.yaml"


def test_daily_path_joins_the_trained_horizons_with_straight_lines() -> None:
    path = daily_path(50.0, {10: 60.0, 20: 40.0}, 20)
    assert len(path) == 20 and path[0] == pytest.approx(51.0) and path[9] == pytest.approx(60.0)
    assert path[14] == pytest.approx(50.0) and path[19] == pytest.approx(40.0)


@pytest.fixture(scope="module")
def definition() -> ScenarioDefinition:
    raw = yaml.safe_load(SCENARIO.read_text())
    raw["time_series"].update({"horizons": [7, 30], "horizon": 30, "backtest": {"windows": 2}})
    return ScenarioDefinition.model_validate(raw)


def _series(years: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    days = pd.date_range("2018-01-01", periods=365 * years, freq="D")
    frames = []
    for name, offset in (("a", 0.0), ("b", 8.0)):
        rain = rng.gamma(0.5, 4.0, len(days))
        level = np.clip(
            50 + offset + 18 * np.sin(2 * np.pi * days.dayofyear / 365.25) + np.cumsum(rain - 2) * 0.03, 0, 100
        )
        frames.append(
            pd.DataFrame({
                "date": days,
                "series": name,
                "pct": level,
                "precip_mm": rain,
                "temp_c": 12 + 8 * np.sin(days.dayofyear / 58),
            })
        )
    return pd.concat(frames, ignore_index=True).set_index("date")


def test_a_run_has_the_shape_training_and_prediction_expect(definition: ScenarioDefinition) -> None:
    run = run_quantile_forecast(_series(), definition, rounds=20, stride=7)
    assert run.selected.name == "lightgbm_quantile"
    assert [c.name for c in run.candidates] == ["persistence", "seasonal_climatology", "lightgbm_quantile"]
    assert all({"mase", "smape", "rmse"} <= set(c.metrics) for c in run.candidates)
    assert run.forecaster is None  # served from the stored forecast, like a statistical winner

    doc = run.forecast
    assert doc["model_name"] == "lightgbm_quantile" and doc["levels"] == ["80"] and doc["horizon"] == 30
    entry = doc["series"][0]
    assert entry["key"] == {"series": "a"} and entry["label"] == "a" and entry["exogenous"] == []
    n = len(entry["forecast"]["ds"])
    band = entry["forecast"]["bands"]["80"]
    assert n == 30 == len(entry["forecast"]["mean"]) == len(band["lower"]) == len(band["upper"])
    assert all(
        lo <= m <= hi + 1e-6 for lo, m, hi in zip(band["lower"], entry["forecast"]["mean"], band["upper"], strict=True)
    )
    assert entry["peak"]["ds"] in entry["forecast"]["ds"] and entry["forecast"]["ds"][0] > entry["history"]["ds"][-1]

    window = run.backtest["windows"][0]["series"][0]
    assert set(window["forecasts"]) == {"persistence", "seasonal_climatology", "lightgbm_quantile"}
    assert all(len(v) == len(window["actual"]) == len(window["ds"]) for v in window["forecasts"].values())
    report = run.metadata_extra["quantile_forecast"]
    assert set(report["by_horizon"]) == {"7", "30"} and report["backtest_years"][1] == 2022
    assert all(math.isfinite(v["mae_pp"]) for v in report["by_horizon"].values())
    assert run.feature_importance and run.training_rows > 0


def test_the_two_exogenous_features_are_required(definition: ScenarioDefinition) -> None:
    df = _series().drop(columns=["temp_c"])
    with pytest.raises(KeyError):
        run_quantile_forecast(df, definition, rounds=5)
    wrong = definition.model_copy(deep=True)
    assert wrong.time_series is not None
    wrong.time_series.exogenous_features = ["precip_mm"]
    with pytest.raises(ValueError, match="reads the exogenous features"):
        run_quantile_forecast(_series(), wrong, rounds=5)
