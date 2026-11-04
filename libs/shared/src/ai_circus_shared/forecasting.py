"""
- Title:    Forecast features and artifacts, shared by training and prediction
- Author:   Angel Martinez-Tenor

A `model.task_type: forecast` scenario (docs/adr/0016-time-series-forecasting.md) is forecast
recursively: each step's inputs are built from the series' own history (lags, rolling
statistics), the calendar and the exogenous features, and each forecast value becomes history
for the next step. Training builds those inputs to fit and backtest the model; prediction builds
them again to re-forecast a what-if. Both call this one module, so the two can never disagree on
a feature. Pure Python on purpose — the shared library carries no numpy/pandas, and a 24-step
forecast is a few hundred numbers.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel

# Written by training next to MODEL_METADATA_KEY (ai_circus_shared.tabular_ml), read by prediction.
MODEL_FORECAST_KEY = "model/forecast.json"  # per series: history tail, forecast, interval bands
MODEL_BACKTEST_KEY = "model/backtest.json"  # per window and candidate: actual vs forecast, metrics
MODEL_FORECASTER_KEY = "model/forecaster.json"  # lightgbm_lags only: features + booster, for what-ifs

FEATURE_GROUP_HISTORY = "Recent history"
FEATURE_GROUP_CALENDAR = "Calendar"

_DATE_PARTS: dict[str, Callable[[datetime], int]] = {
    "hour": lambda d: d.hour,
    "dayofweek": lambda d: d.weekday(),
    "day": lambda d: d.day,
    "month": lambda d: d.month,
    "dayofyear": lambda d: d.timetuple().tm_yday,
    "quarter": lambda d: (d.month - 1) // 3 + 1,
}


def _stat(values: Sequence[float], stat: str) -> float:
    if stat == "min":
        return min(values)
    if stat == "max":
        return max(values)
    mean = sum(values) / len(values)
    if stat == "mean":
        return mean
    return math.sqrt(sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1))  # std (sample)


class ForecastFeatures(BaseModel):
    """The input layout of a `lightgbm_lags` forecaster, stored with the model.

    `categorical` maps each series id / non-numeric static column to its categories; the feature
    value is the category's position (an unseen category is NaN, which LightGBM routes as missing).
    """

    lags: list[int]
    rolling: list[tuple[int, str]] = []  # (window, stat), over the values just before the step
    date_features: list[str] = []
    categorical: dict[str, list[str]] = {}
    numeric_static: list[str] = []
    exogenous: list[str] = []

    def names(self) -> list[str]:
        """Column names, in the order `row()` fills them."""
        return [
            *(f"lag_{lag}" for lag in self.lags),
            *(f"rolling_{stat}_{window}" for window, stat in self.rolling),
            *self.date_features,
            *self.categorical,
            *self.numeric_static,
            *self.exogenous,
        ]

    def lookback(self) -> int:
        """How many past values a row needs."""
        return max([*self.lags, *(window for window, _ in self.rolling)])

    def group(self, name: str) -> str:
        """The reader-facing feature a column belongs to: lags and rolling statistics are the
        series' recent history, date parts the calendar; the rest keep their own name."""
        if name.startswith(("lag_", "rolling_")):
            return FEATURE_GROUP_HISTORY
        if name in self.date_features:
            return FEATURE_GROUP_CALENDAR
        return name

    def row(
        self,
        history: Sequence[float],
        when: datetime,
        exogenous: Mapping[str, float],
        static: Mapping[str, Any],
    ) -> list[float]:
        """One step's inputs. `history`: the series' values strictly before `when`, oldest first,
        at least `lookback()` of them."""
        if len(history) < self.lookback():
            raise ValueError(f"need {self.lookback()} past values, got {len(history)}")
        values: list[float] = [float(history[-lag]) for lag in self.lags]
        values += [_stat(history[-window:], stat) for window, stat in self.rolling]
        values += [float(_DATE_PARTS[part](when)) for part in self.date_features]
        for column, categories in self.categorical.items():
            value = str(static.get(column))
            values.append(float(categories.index(value)) if value in categories else math.nan)
        values += [float(static[column]) for column in self.numeric_static]
        values += [float(exogenous[column]) for column in self.exogenous]
        return values


def recursive_forecast(
    predict: Callable[[list[float]], float],
    features: ForecastFeatures,
    history: Sequence[float],
    times: Sequence[datetime],
    exogenous: Sequence[Mapping[str, float]],
    static: Mapping[str, Any],
) -> list[float]:
    """Forecast `len(times)` steps after `history`, feeding each forecast back as history."""
    values = list(history)
    forecast: list[float] = []
    for when, exog in zip(times, exogenous, strict=True):
        value = float(predict(features.row(values, when, exog, static)))
        forecast.append(value)
        values.append(value)
    return forecast


def future_exogenous(
    past: Sequence[Mapping[str, float]], horizon: int, season_length: int, rule: str
) -> list[dict[str, float]]:
    """The exogenous values assumed for the next `horizon` steps: those of one season earlier
    (`seasonal_naive`; the last value when the history is shorter than a season) or the last
    observed ones (`last_value`). A stand-in for a real forecast of them (a weather forecast)."""
    if not past:
        return [{} for _ in range(horizon)]
    if rule == "seasonal_naive" and len(past) >= season_length > 1:
        season = past[-season_length:]
        return [dict(season[step % season_length]) for step in range(horizon)]
    return [dict(past[-1]) for _ in range(horizon)]


def shift_exogenous(rows: Sequence[Mapping[str, float]], shifts: Mapping[str, float]) -> list[dict[str, float]]:
    """A what-if: every row's exogenous values moved by `shifts` (feature -> amount)."""
    return [{column: value + shifts.get(column, 0.0) for column, value in row.items()} for row in rows]


def apply_floor(values: Sequence[float], floor: float | None) -> list[float]:
    """`values` no lower than the target's physical minimum (`time_series.target_min`), if any."""
    return list(values) if floor is None else [max(v, floor) for v in values]


def interval_bands(
    forecast: Sequence[float], half_widths: Mapping[str, float], scale: float, floor: float | None = None
) -> dict[str, dict[str, list[float]]]:
    """Conformal bands around a forecast: `half_widths` are per level ("80", "95"), in units of
    the series' `scale` (its seasonal-naive mean absolute error), as training calibrated them.
    `floor` cuts them at the target's physical minimum (raising a bound can only add coverage)."""
    return {
        level: {
            "lower": [round(v, 4) for v in apply_floor([v - width * scale for v in forecast], floor)],
            "upper": [round(v + width * scale, 4) for v in forecast],
        }
        for level, width in half_widths.items()
    }


def conformal_half_width(scaled_errors: Sequence[float], level: int) -> float:
    """The split-conformal quantile of absolute scaled backtest errors for a `level`% band (with
    the finite-sample correction, so a band from few errors is wider, not falsely tight)."""
    errors = sorted(abs(e) for e in scaled_errors if math.isfinite(e))
    if not errors:
        return math.inf
    rank = math.ceil((len(errors) + 1) * level / 100)
    return errors[min(rank, len(errors)) - 1]
