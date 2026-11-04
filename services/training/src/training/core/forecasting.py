"""
- Title:    Forecast training: rolling-origin backtest, Green Code selection, conformal bands
- Author:   Angel Martinez-Tenor

A `model.task_type: forecast` scenario (docs/adr/0016-time-series-forecasting.md). Every candidate
in scenario.yaml is backtested on the last `backtest.windows` cut-offs — trained on the data before
each cut-off, scored on the `horizon` steps after it, with the future exogenous values assumed the
way production assumes them — and compared by MASE (mean absolute error over the seasonal-naive
*in-sample* error: a scale shared by the candidates, so out-of-sample values above 1.0 are normal and
the seasonal_naive candidate's own score is the baseline to beat). The simplest candidate within
`accuracy_gain_threshold_for_complexity` of a more complex one is kept, refit on all the data and
forecast `horizon` steps ahead; the backtest errors calibrate its prediction bands (split
conformal), so every candidate's bands are honest in the same way.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from ai_circus_shared.forecasting import (
    ForecastFeatures,
    apply_floor,
    conformal_half_width,
    future_exogenous,
    interval_bands,
    recursive_forecast,
)
from ai_circus_shared.scenario_schema import TabularDataset, TimeSeriesConfig
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from training.core.logger import get_logger

logger = get_logger(__name__)

FORECAST_METRICS = ("mase", "smape", "rmse")  # all lower-is-better
HISTORY_TAIL_MAX = 500  # points of history per series in forecast.json
BACKTEST_SERIES_STORED = 12  # series per window kept in backtest.json (metrics use all)
IMPORTANCE_SAMPLE_ROWS = 2_000

# Sized like training.core.training's small-data LightGBM: a few thousand rows per scenario.
LGBM_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": 0,
    "verbosity": -1,
    "num_threads": 2,
}
LGBM_ROUNDS = 300


@dataclass
class Series:
    """One series, oldest first: target values, timestamps, exogenous rows, static values."""

    key: dict[str, str]
    times: list[datetime]
    y: list[float]
    exogenous: list[dict[str, float]]
    static: dict[str, Any]

    def head(self, n: int) -> Series:
        """The first `n` observations (what a backtest window trains on)."""
        return Series(self.key, self.times[:n], self.y[:n], self.exogenous[:n], self.static)

    @property
    def label(self) -> str:
        """How the series is named on screen: its id values, or "all" for a single series."""
        return " / ".join(self.key.values()) or "all"


@dataclass
class ForecastCandidate:
    """One candidate's backtest: metrics averaged over windows x series, and the errors behind them."""

    name: str
    metrics: dict[str, float]
    scaled_errors: list[float] = field(default_factory=list)
    window_forecasts: list[list[list[float]]] = field(default_factory=list)  # [window][series][step]

    @property
    def selection_score(self) -> float:
        """The backtest MASE."""
        return self.metrics["mase"]

    @property
    def test_score(self) -> float:
        """The backtest MASE, under the name mlflow_tracking reads."""
        return self.metrics["mase"]


# A fitted candidate: (series to continue, future times, future exogenous rows) -> forecast values.
Forecaster = Callable[[Series, Sequence[datetime], Sequence[dict[str, float]]], list[float]]


def split_series(df: pd.DataFrame, dataset: TabularDataset, ts: TimeSeriesConfig) -> list[Series]:
    """etl-tabular's normalized series parquet (sorted, regular) as one `Series` per id."""
    frame = df.reset_index() if df.index.name == ts.time_column else df
    groups = frame.groupby(ts.series_id_columns, observed=True, sort=True) if ts.series_id_columns else [((), frame)]
    series: list[Series] = []
    for name, rows in groups:
        values = name if isinstance(name, tuple) else (name,)
        exogenous = pd.DataFrame(rows[ts.exogenous_features]).astype(float)
        series.append(
            Series(
                key={column: str(v) for column, v in zip(ts.series_id_columns, values, strict=True)},
                times=[t.to_pydatetime() for t in pd.to_datetime(rows[ts.time_column])],
                y=[float(v) for v in rows[dataset.target]],
                exogenous=[{str(k): float(v) for k, v in row.items()} for row in exogenous.to_dict("records")],
                static=rows[[*ts.series_id_columns, *ts.static_features]].iloc[0].to_dict(),
            )
        )
    return series


def seasonal_scale(y: Sequence[float], season_length: int) -> float:
    """MASE's denominator: the in-sample mean absolute error of the seasonal-naive forecast
    (naive when the series is shorter than two seasons); never 0.
    """
    m = season_length if len(y) > season_length else 1
    diffs = [abs(y[i] - y[i - m]) for i in range(m, len(y))]
    return max(sum(diffs) / len(diffs), 1e-9) if diffs else 1.0


def forecast_metrics(actual: Sequence[float], forecast: Sequence[float], scale: float) -> dict[str, float]:
    """MASE, sMAPE (%) and RMSE of one forecast window."""
    errors = [f - a for a, f in zip(actual, forecast, strict=True)]
    smape_terms = [
        200 * abs(e) / (abs(a) + abs(f)) for a, f, e in zip(actual, forecast, errors, strict=True) if abs(a) + abs(f)
    ]
    return {
        "mase": sum(abs(e) for e in errors) / len(errors) / scale,
        "smape": sum(smape_terms) / len(smape_terms) if smape_terms else 0.0,
        "rmse": math.sqrt(sum(e * e for e in errors) / len(errors)),
    }


def features_for(ts: TimeSeriesConfig, series: Sequence[Series]) -> ForecastFeatures:
    """The `lightgbm_lags` inputs of a scenario: its lags/rolling/calendar/exogenous, series ids and
    non-numeric static columns as categories, numeric static columns as numbers.
    """
    categorical: dict[str, list[str]] = {}
    numeric_static: list[str] = []
    for column in [*ts.series_id_columns, *ts.static_features]:
        values = [s.static[column] for s in series]
        if column in ts.series_id_columns or not all(isinstance(v, int | float) for v in values):
            categorical[column] = sorted({str(v) for v in values})
        else:
            numeric_static.append(column)
    return ForecastFeatures(
        lags=ts.lags,
        rolling=[(r.window, r.stat) for r in ts.rolling],
        date_features=list(ts.date_features),
        categorical=categorical,
        numeric_static=numeric_static,
        exogenous=ts.exogenous_features,
    )


def training_matrix(features: ForecastFeatures, series: Sequence[Series]) -> tuple[np.ndarray, np.ndarray]:
    """One row per observation with a full look-back, across every series (a global model)."""
    rows: list[list[float]] = []
    targets: list[float] = []
    lookback = features.lookback()
    for s in series:
        for t in range(lookback, len(s.y)):
            rows.append(features.row(s.y[:t], s.times[t], s.exogenous[t], s.static))
            targets.append(s.y[t])
    return np.asarray(rows, dtype=float), np.asarray(targets, dtype=float)


def fit_lightgbm(features: ForecastFeatures, series: Sequence[Series]) -> lgb.Booster:
    """LightGBM on the features of `series`; series ids/categorical static columns as categorical."""
    x, y = training_matrix(features, series)
    if len(y) < 2 * LGBM_PARAMS["min_data_in_leaf"]:
        raise ValueError(f"lightgbm_lags needs more history: {len(y)} rows with a full look-back")
    names = features.names()
    data = lgb.Dataset(x, y, feature_name=names, categorical_feature=list(features.categorical) or "auto")
    return lgb.train(LGBM_PARAMS, data, num_boost_round=LGBM_ROUNDS)


def _booster_forecaster(booster: lgb.Booster, features: ForecastFeatures) -> Forecaster:
    def predict_row(row: list[float]) -> float:
        return float(np.asarray(booster.predict(np.asarray([row], dtype=float)))[0])

    def forecast(s: Series, times: Sequence[datetime], exog: Sequence[dict[str, float]]) -> list[float]:
        return recursive_forecast(predict_row, features, s.y, times, exog, s.static)

    return forecast


def _seasonal_naive(season_length: int) -> Forecaster:
    def forecast(s: Series, times: Sequence[datetime], _exog: Sequence[dict[str, float]]) -> list[float]:
        if len(s.y) < season_length:
            return [s.y[-1]] * len(times)
        season = s.y[-season_length:]
        return [season[step % season_length] for step in range(len(times))]

    return forecast


def _auto_ets(season_length: int) -> Forecaster:
    """Additive exponential smoothing per series: no trend or a damped trend, additive seasonality
    when two full seasons exist — the configuration with the lowest AIC.
    """

    def forecast(s: Series, times: Sequence[datetime], _exog: Sequence[dict[str, float]]) -> list[float]:
        y = np.asarray(s.y, dtype=float)
        seasonal = "add" if season_length > 1 and len(y) >= 2 * season_length else None
        best = None
        for trend in (None, "add"):
            model = ExponentialSmoothing(
                y,
                trend=trend,
                damped_trend=trend is not None,
                seasonal=seasonal,
                seasonal_periods=season_length if seasonal else None,
                initialization_method="estimated",
            )
            try:
                fitted = model.fit(optimized=True)
            except (ValueError, np.linalg.LinAlgError):
                continue
            if best is None or fitted.aic < best.aic:
                best = fitted
        if best is None:
            return _seasonal_naive(season_length)(s, times, _exog)
        return [float(v) for v in best.forecast(len(times))]

    return forecast


def fit_candidate(
    name: str, ts: TimeSeriesConfig, features: ForecastFeatures, series: Sequence[Series]
) -> tuple[Forecaster, lgb.Booster | None]:
    """A candidate fitted on `series` (the booster too, for `lightgbm_lags`), its forecasts cut at
    `time_series.target_min`.
    """
    season_length = ts.resolved_season_length()
    booster = None
    if name == "seasonal_naive":
        fitted = _seasonal_naive(season_length)
    elif name == "auto_ets":
        fitted = _auto_ets(season_length)
    elif name == "lightgbm_lags":
        booster = fit_lightgbm(features, series)
        fitted = _booster_forecaster(booster, features)
    else:
        raise ValueError(f"unknown forecast candidate {name!r}")

    def forecaster(s: Series, times: Sequence[datetime], exog: Sequence[dict[str, float]]) -> list[float]:
        return apply_floor(fitted(s, times, exog), ts.target_min)

    return forecaster, booster


def backtest_cutoffs(ts: TimeSeriesConfig, length: int) -> list[int]:
    """Training lengths of the backtest windows, oldest first: the last window ends at the last
    observation, each earlier one `backtest.step` (default: the horizon) steps before.
    """
    step = ts.backtest.step or ts.horizon
    cutoffs = [length - ts.horizon - k * step for k in reversed(range(ts.backtest.windows))]
    return [c for c in cutoffs if c > 0]


def backtest(
    name: str, ts: TimeSeriesConfig, features: ForecastFeatures, series: Sequence[Series]
) -> ForecastCandidate:
    """Rolling-origin backtest of one candidate over every series (series of different lengths
    are cut at the same distance from their own end).
    """
    season_length = ts.resolved_season_length()
    per_window: list[dict[str, float]] = []
    candidate = ForecastCandidate(name=name, metrics={})
    windows = backtest_cutoffs(ts, min(len(s.y) for s in series))
    for distance in (min(len(s.y) for s in series) - c for c in windows):
        train = [s.head(len(s.y) - distance) for s in series]
        forecaster, _ = fit_candidate(name, ts, features, train)
        window_forecasts: list[list[float]] = []
        for full, part in zip(series, train, strict=True):
            n = len(part.y)
            times, actual = full.times[n : n + ts.horizon], full.y[n : n + ts.horizon]
            exog = future_exogenous(part.exogenous, len(times), season_length, ts.future_exogenous)
            forecast = forecaster(part, times, exog)
            scale = seasonal_scale(part.y, season_length)
            per_window.append(forecast_metrics(actual, forecast, scale))
            candidate.scaled_errors += [(f - a) / scale for a, f in zip(actual, forecast, strict=True)]
            window_forecasts.append([round(v, 4) for v in forecast])
        candidate.window_forecasts.append(window_forecasts)
    candidate.metrics = {m: round(sum(w[m] for w in per_window) / len(per_window), 4) for m in FORECAST_METRICS}
    logger.info("Backtest {}: {}", name, candidate.metrics)
    return candidate


def select_forecast_candidate(
    candidates: Sequence[ForecastCandidate], metric: str, threshold: float
) -> ForecastCandidate:
    """Green Code, lower-is-better: candidates are simplest first; a more complex one replaces the
    current best only when it lowers `metric` by more than `threshold`.
    """
    best = candidates[0]
    for candidate in candidates[1:]:
        gain = best.metrics[metric] - candidate.metrics[metric]
        if gain > threshold:
            logger.info("Selecting {!r} over {!r}: {} lower by {:.4f}", candidate.name, best.name, metric, gain)
            best = candidate
        else:
            logger.info(
                "Keeping simpler {!r} over {!r}: {} gain {:.4f} <= {:.4f}",
                best.name,
                candidate.name,
                metric,
                gain,
                threshold,
            )
    return best


def future_times(last: datetime, frequency: str, horizon: int) -> list[datetime]:
    """The `horizon` timestamps after `last` at `frequency`."""
    return [t.to_pydatetime() for t in pd.date_range(last, periods=horizon + 1, freq=frequency)[1:]]


def grouped_importance(
    booster: lgb.Booster, features: ForecastFeatures, series: Sequence[Series]
) -> list[dict[str, Any]]:
    """Mean |SHAP| of the LightGBM (its native tree SHAP, `pred_contrib`) over recent rows, summed
    into what a reader recognises: recent history, calendar, and each exogenous/static feature.
    """
    x, _ = training_matrix(features, series)
    x = x[-IMPORTANCE_SAMPLE_ROWS:]
    contributions = np.abs(np.asarray(booster.predict(x, pred_contrib=True))[:, :-1]).mean(axis=0)
    totals: dict[str, float] = {}
    for name, value in zip(features.names(), contributions, strict=True):
        group = features.group(name)
        totals[group] = totals.get(group, 0.0) + float(value)
    return [{"feature": k, "importance": round(v, 4)} for k, v in sorted(totals.items(), key=lambda kv: -kv[1])]


def _iso(times: Sequence[datetime]) -> list[str]:
    return [t.isoformat() for t in times]


@dataclass
class ForecastRun:
    """Everything training writes for a forecast scenario."""

    candidates: list[ForecastCandidate]
    selected: ForecastCandidate
    forecast: dict[str, Any]
    backtest: dict[str, Any]
    forecaster: dict[str, Any] | None
    feature_importance: list[dict[str, Any]] | None
    training_rows: int


def train_forecast(
    candidate_names: Sequence[str],
    ts: TimeSeriesConfig,
    series: Sequence[Series],
    selection_metric: str,
    threshold: float,
) -> ForecastRun:
    """Backtest every candidate, select one, refit it on all the data and forecast ahead."""
    features = features_for(ts, series)
    season_length = ts.resolved_season_length()
    candidates = [backtest(name, ts, features, series) for name in candidate_names]
    selected = select_forecast_candidate(candidates, selection_metric, threshold)
    half_widths = {str(level): conformal_half_width(selected.scaled_errors, level) for level in ts.prediction_intervals}

    forecaster, booster = fit_candidate(selected.name, ts, features, series)
    tail = min(HISTORY_TAIL_MAX, max(4 * ts.horizon, 2 * season_length))
    forecast_series: list[dict[str, Any]] = []
    whatif_series: list[dict[str, Any]] = []
    for s in series:
        times = future_times(s.times[-1], ts.frequency, ts.horizon)
        exog = future_exogenous(s.exogenous, ts.horizon, season_length, ts.future_exogenous)
        values = [round(v, 4) for v in forecaster(s, times, exog)]
        scale = seasonal_scale(s.y, season_length)
        peak = max(range(len(values)), key=values.__getitem__)
        forecast_series.append({
            "key": s.key,
            "label": s.label,
            "history": {"ds": _iso(s.times[-tail:]), "y": [round(v, 4) for v in s.y[-tail:]]},
            "forecast": {
                "ds": _iso(times),
                "mean": values,
                "bands": interval_bands(values, half_widths, scale, ts.target_min),
            },
            "exogenous": exog,
            "peak": {"ds": times[peak].isoformat(), "value": values[peak]},
        })
        if booster is not None:
            whatif_series.append({
                "key": s.key,
                "history": s.y[-features.lookback() :],
                "times": _iso(times),
                "exogenous": exog,
                "static": {k: (v if isinstance(v, int | float) else str(v)) for k, v in s.static.items()},
                "scale": scale,
            })

    windows = backtest_cutoffs(ts, min(len(s.y) for s in series))
    shortest = min(len(s.y) for s in series)
    backtest_doc = {
        "selection_metric": selection_metric,
        "selected": selected.name,
        "horizon": ts.horizon,
        "candidates": [{"name": c.name, "metrics": c.metrics} for c in candidates],
        "windows": [
            {
                "cutoff": series[0].times[len(series[0].y) - (shortest - cut) - 1].isoformat(),
                "series": [
                    {
                        "key": s.key,
                        "label": s.label,
                        "ds": _iso(s.times[len(s.y) - (shortest - cut) :][: ts.horizon]),
                        "actual": [round(v, 4) for v in s.y[len(s.y) - (shortest - cut) :][: ts.horizon]],
                        "forecasts": {c.name: c.window_forecasts[w][i] for c in candidates},
                    }
                    for i, s in enumerate(series[:BACKTEST_SERIES_STORED])
                ],
            }
            for w, cut in enumerate(windows)
        ],
    }
    forecast_doc = {
        "model_name": selected.name,
        "frequency": ts.frequency,
        "horizon": ts.horizon,
        "levels": [str(level) for level in ts.prediction_intervals],
        "series": forecast_series,
    }
    forecaster_doc = None
    importance = None
    if booster is not None:
        forecaster_doc = {
            "features": features.model_dump(),
            "model": booster.model_to_string(),
            "half_widths": half_widths,
            "floor": ts.target_min,
            "series": whatif_series,
        }
        importance = grouped_importance(booster, features, series)
    rows = sum(max(0, len(s.y) - features.lookback()) for s in series)
    return ForecastRun(candidates, selected, forecast_doc, backtest_doc, forecaster_doc, importance, rows)
