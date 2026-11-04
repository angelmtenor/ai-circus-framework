"""ai_circus_shared.forecasting — the features training and prediction both build."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from ai_circus_shared.forecasting import (
    FEATURE_GROUP_CALENDAR,
    FEATURE_GROUP_HISTORY,
    ForecastFeatures,
    apply_floor,
    conformal_half_width,
    future_exogenous,
    interval_bands,
    recursive_forecast,
    shift_exogenous,
)

FEATURES = ForecastFeatures(
    lags=[1, 3],
    rolling=[(2, "mean"), (3, "max")],
    date_features=["hour", "dayofweek", "quarter"],
    categorical={"site": ["a", "b"]},
    numeric_static=["size"],
    exogenous=["temp"],
)


def test_row_follows_names_and_reads_only_the_past() -> None:
    when = datetime(2026, 11, 3, 18)  # a Tuesday
    row = FEATURES.row([1.0, 2.0, 3.0, 4.0], when, {"temp": 9.5}, {"site": "b", "size": 3})
    assert FEATURES.names() == [
        "lag_1", "lag_3", "rolling_mean_2", "rolling_max_3", "hour", "dayofweek", "quarter", "site", "size", "temp",
    ]  # fmt: skip
    assert row == [4.0, 2.0, 3.5, 4.0, 18.0, 1.0, 4.0, 1.0, 3.0, 9.5]
    assert math.isnan(FEATURES.row([1, 2, 3], when, {"temp": 0}, {"site": "zzz", "size": 1})[7])
    with pytest.raises(ValueError, match="need 3 past values"):
        FEATURES.row([1.0, 2.0], when, {"temp": 0}, {"site": "a", "size": 1})


def test_groups_and_lookback() -> None:
    assert FEATURES.lookback() == 3
    assert FEATURES.group("lag_3") == FEATURES.group("rolling_max_3") == FEATURE_GROUP_HISTORY
    assert FEATURES.group("hour") == FEATURE_GROUP_CALENDAR
    assert FEATURES.group("temp") == "temp"


def test_recursive_forecast_feeds_each_value_back() -> None:
    features = ForecastFeatures(lags=[1])
    start = datetime(2026, 1, 1)
    times = [start + timedelta(hours=h) for h in range(3)]
    forecast = recursive_forecast(lambda row: row[0] + 1, features, [10.0], times, [{}, {}, {}], {})
    assert forecast == [11.0, 12.0, 13.0]


def test_future_exogenous_rules_and_what_if_shifts() -> None:
    past = [{"temp": float(t)} for t in range(10)]
    assert [r["temp"] for r in future_exogenous(past, 5, 4, "seasonal_naive")] == [6, 7, 8, 9, 6]
    assert [r["temp"] for r in future_exogenous(past, 2, 4, "last_value")] == [9, 9]
    assert [r["temp"] for r in future_exogenous(past[:2], 2, 4, "seasonal_naive")] == [1, 1]  # < one season
    assert shift_exogenous([{"temp": 1.0, "rh": 50.0}], {"temp": -3}) == [{"temp": -2.0, "rh": 50.0}]


def test_conformal_half_width_and_bands() -> None:
    errors = [0.1 * i for i in range(1, 20)]  # 19 errors -> the 80% band is the 16th smallest
    assert conformal_half_width(errors, 80) == pytest.approx(1.6)
    assert conformal_half_width(errors[:3], 95) == pytest.approx(0.3)  # too few: the widest error
    assert conformal_half_width([], 80) == math.inf
    assert interval_bands([10.0], {"80": 0.5}, scale=4.0) == {"80": {"lower": [8.0], "upper": [12.0]}}
    # A physical minimum cuts the lower bound only.
    assert interval_bands([1.0], {"80": 0.5}, scale=4.0, floor=0.0) == {"80": {"lower": [0.0], "upper": [3.0]}}
    assert apply_floor([-1.0, 2.0], 0.0) == [0.0, 2.0]
    assert apply_floor([-1.0], None) == [-1.0]
