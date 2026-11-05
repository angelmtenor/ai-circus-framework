"""Forecast training (docs/adr/0016-time-series-forecasting.md) on small synthetic series."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest
from ai_circus_shared.forecasting import (
    MODEL_BACKTEST_KEY,
    MODEL_FORECAST_KEY,
    MODEL_FORECASTER_KEY,
    ForecastFeatures,
    interval_bands,
    recursive_forecast,
)
from ai_circus_shared.scenario_schema import ScenarioDefinition, TabularDataset, TimeSeriesConfig
from ai_circus_shared.tabular_ml import MODEL_CHECKSUMS_METADATA_FIELD, MODEL_METADATA_KEY, artifact_checksum

from tests.test_app import _run_main
from training.core.forecasting import (
    ForecastCandidate,
    backtest_cutoffs,
    features_for,
    forecast_metrics,
    future_times,
    seasonal_scale,
    select_forecast_candidate,
    split_series,
    train_forecast,
)

DATASET = TabularDataset(
    bucket="scenario-x",
    raw_object="raw/x.csv",
    seed_file="sample_data/x.csv",
    index_col="date",
    target="load",
    feature_columns=["site", "temp"],
    feature_schema={
        "site": {"type": "categorical", "label": "Site", "options": ["a", "b"], "default": "a"},
        "temp": {"type": "numeric", "label": "Temp", "min": -10, "max": 40, "default": 10},
    },
)
SERIES = TimeSeriesConfig(
    time_column="date",
    series_id_columns=["site"],
    frequency="h",
    horizon=6,
    lags=[1, 2, 24],
    date_features=["hour"],
    rolling=[{"window": 6, "stat": "mean"}],
    exogenous_features=["temp"],
    backtest={"windows": 2, "step": 6},
)


def _hourly_sites(hours: int = 24 * 12) -> pd.DataFrame:
    """Two sites with a strong daily cycle, a temperature effect and a little noise."""
    rng = np.random.default_rng(0)
    frames = []
    for site, level in (("a", 100.0), ("b", 40.0)):
        times = pd.date_range("2026-01-01", periods=hours, freq="h")
        temp = 10 + 5 * np.sin(np.arange(hours) / 24 * 2 * np.pi) + rng.normal(0, 0.5, hours)
        load = level + 30 * np.sin(np.arange(hours) / 24 * 2 * np.pi + 1) + 2 * temp + rng.normal(0, 2, hours)
        frames.append(pd.DataFrame({"date": times, "site": site, "temp": temp, "load": load}))
    frame = pd.concat(frames, ignore_index=True)
    frame["site"] = frame["site"].astype("category")
    return frame.set_index("date")


def test_split_series_one_per_id_with_its_static_values() -> None:
    series = split_series(_hourly_sites(48), DATASET, SERIES)
    assert [s.key for s in series] == [{"site": "a"}, {"site": "b"}]
    assert len(series[0].y) == 48 and series[0].static == {"site": "a"}
    assert set(series[0].exogenous[0]) == {"temp"}


def test_metrics_and_scale() -> None:
    assert seasonal_scale([1, 2, 3, 4], 2) == pytest.approx(2.0)
    assert seasonal_scale([5.0], 24) == pytest.approx(1.0)
    metrics = forecast_metrics([10, 20], [12, 18], scale=2.0)
    assert metrics["mase"] == pytest.approx(1.0) and metrics["rmse"] == pytest.approx(2.0)
    assert math.isclose(metrics["smape"], (200 * 2 / 22 + 200 * 2 / 38) / 2)


def test_selection_is_lower_is_better_under_the_green_code_threshold() -> None:
    naive = ForecastCandidate("seasonal_naive", {"mase": 1.00, "smape": 0, "rmse": 0})
    ets = ForecastCandidate("auto_ets", {"mase": 0.97, "smape": 0, "rmse": 0})
    gbm = ForecastCandidate("lightgbm_lags", {"mase": 0.80, "smape": 0, "rmse": 0})
    assert select_forecast_candidate([naive, ets], "mase", 0.05).name == "seasonal_naive"  # 0.03 is not enough
    assert select_forecast_candidate([naive, ets, gbm], "mase", 0.05).name == "lightgbm_lags"


def test_backtest_cutoffs_and_future_times() -> None:
    assert backtest_cutoffs(SERIES, 100) == [88, 94]
    times = future_times(pd.Timestamp("2026-01-01 22:00").to_pydatetime(), "h", 3)
    assert [t.hour for t in times] == [23, 0, 1]


def test_train_forecast_selects_backtests_and_forecasts_every_series() -> None:
    series = split_series(_hourly_sites(), DATASET, SERIES)
    run = train_forecast(["seasonal_naive", "auto_ets", "lightgbm_lags"], SERIES, series, "mase", 0.05)
    assert [c.name for c in run.candidates] == ["seasonal_naive", "auto_ets", "lightgbm_lags"]
    assert all(set(c.metrics) == {"mase", "smape", "rmse"} for c in run.candidates)
    first = run.forecast["series"][0]
    assert len(first["forecast"]["mean"]) == SERIES.horizon
    lower80, upper80 = first["forecast"]["bands"]["80"]["lower"], first["forecast"]["bands"]["80"]["upper"]
    lower95 = first["forecast"]["bands"]["95"]["lower"]
    assert all(
        lo95 <= lo <= m <= hi
        for lo95, lo, m, hi in zip(lower95, lower80, first["forecast"]["mean"], upper80, strict=True)
    )
    assert first["peak"]["value"] == max(first["forecast"]["mean"])
    assert len(run.backtest["windows"]) == 2
    window = run.backtest["windows"][0]["series"][0]
    assert len(window["actual"]) == SERIES.horizon and set(window["forecasts"]) == {c.name for c in run.candidates}


def test_numeric_static_features_stay_numbers_for_the_forecaster() -> None:
    """Whether they arrive as Python or numpy numbers, and through the stored what-if document."""
    config = SERIES.model_copy(update={"static_features": ["size"]})
    frame = _hourly_sites().assign(size=lambda d: d["site"].map({"a": 10, "b": 20}).astype(int))
    series = split_series(frame, DATASET, config)
    features = features_for(config, series)
    assert features.numeric_static == ["size"] and list(features.categorical) == ["site"]
    run = train_forecast(["lightgbm_lags"], config, series, "mase", 0.05)
    assert run.forecaster is not None
    json.dumps(run.forecaster)  # a numpy number here would fail the artifact's upload
    assert run.forecaster["series"][1]["static"] == {"site": "b", "size": 20.0}


def test_target_min_floors_forecasts_and_bands_for_every_candidate() -> None:
    config = SERIES.model_copy(update={"target_min": 0.0})
    series = split_series(_hourly_sites(), DATASET, config)
    # The series is far above 0 except for its noise, so pull it down to force negative forecasts.
    for s in series:
        s.y = [v - 200 for v in s.y]
    for name in ("seasonal_naive", "auto_ets", "lightgbm_lags"):
        run = train_forecast([name], config, series, "mase", 0.05)
        forecast = run.forecast["series"][0]["forecast"]
        assert min(forecast["mean"]) >= 0
        assert all(min(band["lower"]) >= 0 for band in forecast["bands"].values())
    assert run.forecaster is not None and run.forecaster["floor"] == 0


def test_the_stored_forecaster_reproduces_the_forecast_through_the_shared_features() -> None:
    """Train/serve parity: prediction re-forecasts from forecaster.json with ai_circus_shared alone."""
    series = split_series(_hourly_sites(), DATASET, SERIES)
    run = train_forecast(["lightgbm_lags"], SERIES, series, "mase", 0.05)
    assert run.forecaster is not None and run.feature_importance
    assert {entry["feature"] for entry in run.feature_importance} <= {"Recent history", "Calendar", "site", "temp"}
    booster = lgb.Booster(model_str=run.forecaster["model"])
    features = ForecastFeatures.model_validate(run.forecaster["features"])
    stored = run.forecaster["series"][1]
    values = recursive_forecast(
        lambda row: float(booster.predict(np.asarray([row]))[0]),
        features,
        stored["history"],
        [pd.Timestamp(t).to_pydatetime() for t in stored["times"]],
        stored["exogenous"],
        stored["static"],
    )
    expected = run.forecast["series"][1]["forecast"]
    assert [round(v, 4) for v in values] == expected["mean"]
    assert (
        interval_bands([round(v, 4) for v in values], run.forecaster["half_widths"], stored["scale"])
        == expected["bands"]
    )


def test_main_trains_a_forecast_scenario_and_writes_checksummed_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    energy = ScenarioDefinition.load(Path(__file__).parents[3] / "scenarios/energy_building/scenario.yaml")
    energy = energy.model_views()["energy_building~forecast"]
    rng = np.random.default_rng(1)
    hours = 24 * 30
    times = pd.date_range("2026-01-01", periods=hours, freq="h")
    cycle = np.sin(np.arange(hours) / 24 * 2 * np.pi)
    df = pd.DataFrame(
        {
            "T_out": 8 + 4 * cycle + rng.normal(0, 0.3, hours),
            "RH_out": 80 + rng.normal(0, 3, hours),
            "Windspeed": 4 + rng.normal(0, 1, hours),
            "Appliances": 120 + 80 * np.maximum(cycle, 0) + rng.normal(0, 10, hours),
        },
        index=pd.Index(times, name="date"),
    )
    store = _run_main(monkeypatch, energy, df)

    metadata = json.loads(store.objects["demo", MODEL_METADATA_KEY])
    assert metadata["task_type"] == "forecast" and metadata["selection_metric"] == "mase"
    assert metadata["time_series"]["horizon"] == 24 and metadata["time_series"]["series"] == 1
    for name, key in (("forecast", MODEL_FORECAST_KEY), ("backtest", MODEL_BACKTEST_KEY)):
        assert metadata[MODEL_CHECKSUMS_METADATA_FIELD][name] == artifact_checksum(store.objects["demo", key])
    forecast = json.loads(store.objects["demo", MODEL_FORECAST_KEY])
    assert forecast["model_name"] == metadata["model_name"] and len(forecast["series"][0]["forecast"]["ds"]) == 24
    assert (("demo", MODEL_FORECASTER_KEY) in store.objects) == (metadata["model_name"] == "lightgbm_lags")
    assert pd.read_parquet(io.BytesIO(store.objects["demo", "processed/normalized.parquet"])).shape == df.shape
