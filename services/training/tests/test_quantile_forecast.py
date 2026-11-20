"""
- Title:    Tests for the direct multi-horizon quantile forecast
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from training.core.quantile_forecast import (
    QuantileConfig,
    backtest,
    climatology,
    explain,
    feature_frame,
    feature_group,
    feature_names,
    fit,
    latest_forecast,
    predict,
    summarise,
)

FAST = QuantileConfig(horizons=(7, 30), rounds=30, stride=5)


def _table(years: int = 5, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.date_range("2015-01-01", periods=365 * years, freq="D")
    frames = []
    for name, offset in (("a", 0.0), ("b", 10.0)):
        rain = rng.gamma(0.5, 4.0, len(days))
        level = 50 + offset + 20 * np.sin(2 * np.pi * days.dayofyear / 365.25) + np.cumsum(rain - rain.mean()) * 0.05
        frames.append(
            pd.DataFrame({
                "series": name,
                "day": days,
                "y": level,
                "precip_mm": rain,
                "temp_c": 12 + 8 * np.sin(days.dayofyear / 58),
            })
        )
    return pd.concat(frames, ignore_index=True)


def test_features_read_only_the_past() -> None:
    table = _table()
    cut = pd.Timestamp("2018-06-01")
    altered = table.copy()
    future = altered["day"] > cut
    altered.loc[future, ["y", "precip_mm", "temp_c"]] = 9_999.0
    a, b = feature_frame(table, (7,)), feature_frame(altered, (7,))
    names = feature_names(a)
    before = a["day"] <= cut
    pd.testing.assert_frame_equal(a.loc[before, names], b.loc[before, names])
    assert not a.loc[before, "target_7"].equals(b.loc[before, "target_7"])  # only the targets look ahead


def test_feature_names_never_include_targets_or_keys() -> None:
    names = feature_names(feature_frame(_table(2), (7, 30)))
    assert not [n for n in names if n.startswith("target_") or n in ("series", "day")]
    assert {"y", "delta_30", "precip_90", "temp_anomaly_30", "doy_sin"} <= set(names)


def test_climatology_ignores_everything_from_the_cutoff_on() -> None:
    frame = feature_frame(_table(), ())
    cut = pd.Timestamp("2018-01-01")
    changed = frame.copy()
    changed.loc[changed["day"] >= cut, "y"] = 1e6
    pd.testing.assert_frame_equal(climatology(frame, cut), climatology(changed, cut))


def test_quantiles_are_ordered_and_floored() -> None:
    frame = feature_frame(_table(), FAST.horizons)
    fitted = fit(frame, QuantileConfig(**{**FAST.__dict__, "floor": 55.0}), target_before=pd.Timestamp("2018-06-01"))
    rows = frame[frame["day"] > "2018-06-01"].dropna(subset=["y"]).iloc[:200]
    pred = predict(fitted, rows)
    for h in FAST.horizons:
        assert (pred[f"p10_{h}"] <= pred[f"p50_{h}"]).all() and (pred[f"p50_{h}"] <= pred[f"p90_{h}"]).all()
        assert (pred[f"p10_{h}"] >= 55.0).all()


def test_fit_never_uses_a_target_dated_after_the_cutoff() -> None:
    frame = feature_frame(_table(), FAST.horizons)
    cut = pd.Timestamp("2018-06-01")
    leaked = frame.copy()
    # moving the future (target dates on or after the cutoff) must not change what is fitted
    leaked.loc[leaked["day"] >= cut, "y"] += 500
    a = fit(frame, FAST, target_before=cut)
    b = fit(leaked, FAST, target_before=cut)
    rows = frame[frame["day"] < cut - pd.Timedelta(days=60)].dropna(subset=["y"]).iloc[-50:]
    pd.testing.assert_frame_equal(predict(a, rows), predict(b, rows))


def test_shap_groups_add_up_to_the_predicted_change() -> None:
    frame = feature_frame(_table(), FAST.horizons)
    fitted = fit(frame, FAST, target_before=pd.Timestamp("2018-06-01"))
    rows = frame[frame["day"] > "2018-09-01"].dropna(subset=["y"]).iloc[:25]
    groups = explain(fitted, rows, 30)
    change = fitted.models[30, 0.5].predict(rows[fitted.names])
    np.testing.assert_allclose(groups.sum(axis=1).to_numpy(), change, atol=1e-6)


def test_feature_groups_are_readable() -> None:
    assert feature_group("delta_90") == "recent trend"
    assert feature_group("precip_30") == "rainfall"
    assert feature_group("temp_anomaly_30") == "temperature"
    assert feature_group("y") == "storage history"
    assert feature_group("doy_cos") == "season"
    assert feature_group("something_new") == "other"


def test_backtest_scores_each_test_year_against_both_baselines() -> None:
    frame = feature_frame(_table(6), FAST.horizons)
    results = backtest(frame, FAST, [2019, 2020], origin_stride=30)
    assert set(results["year"]) == {2019, 2020}
    assert {"persistence", "climatology", "seasonal_drift", "lo", "p50", "hi"} <= set(results.columns)
    assert results["day"].dt.year.eq(results["year"]).all()
    summary = summarise(results)
    assert list(summary.index) == [7, 30]
    assert summary["beats_persistence"].dtype == bool


def test_summarise_counts_coverage_and_errors() -> None:
    results = pd.DataFrame({
        "horizon": [7, 7, 7, 7],
        "actual": [10.0, 20.0, 30.0, 40.0],
        "persistence": [12.0, 20.0, 30.0, 40.0],
        "climatology": [50.0] * 4,
        "seasonal_drift": [10.0] * 4,
        "lo": [9.0, 21.0, 29.0, 39.0],
        "p50": [10.0, 22.0, 30.0, 40.0],
        "hi": [11.0, 25.0, 31.0, 41.0],
    })
    row = summarise(results).loc[7]
    assert row["coverage"] == pytest.approx(0.75)  # the second actual (20) is below its band
    assert row["mae_model"] == pytest.approx(0.5) and row["mae_persistence"] == pytest.approx(0.5)
    assert not row["beats_persistence"] and row["beats_climatology"]


def test_latest_forecast_has_every_series_horizon_and_driver() -> None:
    frame = feature_frame(_table(), FAST.horizons)
    fitted = fit(frame, FAST, target_before=frame["day"].max())
    out = latest_forecast(frame, fitted)
    assert [e["series"] for e in out] == ["a", "b"]
    first = out[0]["horizons"]["30"]
    assert first["p10"] <= first["p50"] <= first["p90"]
    assert "base" in first["drivers_pp"]


def test_a_missing_driver_column_is_not_required() -> None:
    table = _table(3).drop(columns=["precip_mm", "temp_c"])
    names = feature_names(feature_frame(table, (7,)))
    assert not [n for n in names if n.startswith(("precip", "temp"))]
    with pytest.raises(KeyError):
        feature_frame(table.drop(columns=["y"]), (7,))
