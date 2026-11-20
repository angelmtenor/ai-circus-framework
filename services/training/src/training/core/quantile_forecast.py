"""
- Title:    Direct multi-horizon quantile forecast (LightGBM) with persistence and climatology baselines
- Author:   Angel Martinez-Tenor

For a daily level series (a storage percentage) that is driven by a past-only exogenous signal (catchment
precipitation, temperature). One LightGBM model per horizon and per quantile predicts the *change* from today's
value, pooled over every series; the horizon set and the quantiles are config. Nothing from the future reaches a
feature: every feature at origin `t` is a function of rows at or before `t`.

`backtest` is a rolling-origin evaluation by test year: each fold trains on origins whose *target date* lies
before the test year, then scores that year's origins against two baselines the model has to beat:
persistence (today's value) and seasonal climatology (mean of the same calendar window in the training years).
It reports MAE in percentage points and the coverage of the central interval. An optional split-conformal
widening (`conformal=True`) calibrates the band on the last training year, never on the test year.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

# pandas' stubs are too loose for pyrefly (every groupby/index is "DataFrame | Series | Unknown"), so frames and
# timestamps are typed Any at this module's boundary and its tests carry the contract.
Frame = Any
Stamp = Any

LAGS = (1, 7, 30, 90, 365)
ROLL_WINDOWS = (7, 30, 90, 365)
DEFAULT_HORIZONS = (7, 30, 90)
DEFAULT_QUANTILES = (0.1, 0.5, 0.9)
CLIMATOLOGY_HALF_WINDOW = 3  # days either side of the calendar day

LGBM_PARAMS: dict[str, Any] = {
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 80,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.7,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "seed": 0,
    "verbosity": -1,
    "num_threads": 2,
}
LGBM_ROUNDS = 250
TRAIN_ORIGIN_STRIDE = 3  # consecutive days are near-duplicates: every third is enough to fit


@dataclass
class QuantileConfig:
    """Which horizons and quantiles to fit, and how hard (rounds, stride, conformal calibration)."""

    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    quantiles: tuple[float, ...] = DEFAULT_QUANTILES
    rounds: int = LGBM_ROUNDS
    stride: int = TRAIN_ORIGIN_STRIDE
    conformal: bool = False
    floor: float = 0.0
    params: dict[str, Any] = field(default_factory=lambda: dict(LGBM_PARAMS))


def feature_frame(table: Frame, horizons: tuple[int, ...] = ()) -> Frame:
    """Features (and, if `horizons`, the targets `target_h` = value at t+h) for every series and day.

    `table` needs `series`, `day`, `y` and, optionally, `precip_mm` and `temp_c`. Every feature reads only the
    past of its row; the targets read the future and are never features.
    """
    frames: list[Frame] = []
    for code, (name, g) in enumerate(table.sort_values(["series", "day"]).groupby("series", sort=True)):
        g = g.set_index("day").asfreq("D")
        y = g["y"]
        f = pd.DataFrame(index=g.index)
        f["series"] = name
        f["series_code"] = code
        f["y"] = y
        for lag in LAGS:
            f[f"delta_{lag}"] = y - y.shift(lag)
        f["same_day_last_year"] = y.shift(365)
        f["max_365"] = y.rolling(365, min_periods=180).max() - y
        f["min_365"] = y - y.rolling(365, min_periods=180).min()
        if "precip_mm" in g:
            precip = g["precip_mm"].fillna(0.0)
            for w in ROLL_WINDOWS:
                f[f"precip_{w}"] = precip.rolling(w, min_periods=int(w * 0.8)).sum()
            # rain of the last 90 days against the same 90 days a year earlier: past only
            f["precip_90_vs_last_year"] = f["precip_90"] - f["precip_90"].shift(365)
        if "temp_c" in g:
            temp30 = g["temp_c"].rolling(30, min_periods=20).mean()
            f["temp_anomaly_30"] = temp30 - temp30.shift(365)
        doy = pd.Series(g.index).dt.dayofyear.to_numpy()
        f["doy_sin"] = np.sin(2 * math.pi * doy / 365.25)
        f["doy_cos"] = np.cos(2 * math.pi * doy / 365.25)
        for h in horizons:
            f[f"target_{h}"] = y.shift(-h)
        frames.append(f.reset_index(names="day"))
    return pd.concat(frames, ignore_index=True)


def feature_names(frame: Frame) -> list[str]:
    """The columns a model reads: everything but the keys and the targets."""
    return [c for c in frame.columns if c not in ("series", "day") and not c.startswith("target_")]


def climatology(frame: Frame, upto: Stamp) -> Frame:
    """Mean value of each series for each calendar day (+-3 days), from the rows before `upto` only."""
    past = frame[(frame["day"] < upto) & frame["y"].notna()]
    out: dict[str, np.ndarray] = {}
    for name, g in past.groupby("series"):
        daily = g.groupby(g["day"].dt.dayofyear)["y"].mean().reindex(range(1, 367))
        padded = pd.concat([daily.iloc[-CLIMATOLOGY_HALF_WINDOW:], daily, daily.iloc[:CLIMATOLOGY_HALF_WINDOW]])
        smooth = padded.rolling(2 * CLIMATOLOGY_HALF_WINDOW + 1, center=True, min_periods=1).mean()
        out[str(name)] = smooth.iloc[CLIMATOLOGY_HALF_WINDOW:-CLIMATOLOGY_HALF_WINDOW].to_numpy()
    return pd.DataFrame(out, index=range(1, 367))


@dataclass
class FittedModels:
    """The fitted boosters (one per horizon and quantile) with what is needed to use them."""

    config: QuantileConfig
    names: list[str]
    models: dict[tuple[int, float], lgb.Booster]
    widening: dict[int, float]  # conformal widening per horizon (pp); 0 without calibration


def _train_rows(frame: Frame, h: int, target_before: Stamp, stride: int) -> Frame:
    rows = frame[
        (frame["day"] + pd.Timedelta(days=h) < target_before) & frame[f"target_{h}"].notna() & frame["y"].notna()
    ]
    return rows.iloc[::stride]


def fit(frame: Frame, config: QuantileConfig, *, target_before: Stamp) -> FittedModels:
    """Fit every (horizon, quantile) on origins whose target date is before `target_before`."""
    names = feature_names(frame)
    models: dict[tuple[int, float], lgb.Booster] = {}
    widening: dict[int, float] = {}
    for h in config.horizons:
        rows = _train_rows(frame, h, target_before, config.stride)
        x, delta = rows[names], rows[f"target_{h}"] - rows["y"]
        for q in config.quantiles:
            params = {**config.params, "objective": "quantile", "alpha": q}
            models[h, q] = lgb.train(params, lgb.Dataset(x, delta, free_raw_data=False), num_boost_round=config.rounds)
        widening[h] = 0.0
    fitted = FittedModels(config, names, models, widening)
    if config.conformal:
        _calibrate(fitted, frame, target_before)
    return fitted


CALIBRATION_DAYS = 365


def _calibrate(fitted: FittedModels, frame: Frame, target_before: Stamp) -> None:
    """Split-conformal widening of the central interval, calibrated out of sample: a second set of models is fit
    on origins whose target lies before the last `CALIBRATION_DAYS` of the training period, and its misses on
    that final year set the widening applied to the models fitted on everything. (Residuals of the final
    models on their own training rows would be too small, and the band too narrow.)
    """
    cfg = fitted.config
    cal_start = target_before - pd.Timedelta(days=CALIBRATION_DAYS)
    inner = fit(frame, replace(cfg, conformal=False), target_before=cal_start)
    lo_q, hi_q = min(cfg.quantiles), max(cfg.quantiles)
    for h in cfg.horizons:
        rows = _train_rows(frame, h, target_before, 1)
        rows = rows[rows["day"] >= cal_start]
        pred = predict(inner, rows, widen=False)
        lo, hi = pred[f"p{round(lo_q * 100)}_{h}"], pred[f"p{round(hi_q * 100)}_{h}"]
        actual = rows[f"target_{h}"].to_numpy()
        score = np.maximum(lo.to_numpy() - actual, actual - hi.to_numpy())
        level = min(1.0, (hi_q - lo_q) * (1 + 1 / max(len(score), 1)))
        fitted.widening[h] = max(0.0, float(np.quantile(score, level)))


def predict(fitted: FittedModels, rows: Frame, *, widen: bool = True) -> Frame:
    """Value at t+h for every row, as `p{q}_{h}` columns: today's value plus the predicted change, with the
    quantiles put in order (no crossing), floored at `floor`, and the central interval widened if calibrated.
    """
    cfg = fitted.config
    out = pd.DataFrame(index=rows.index)
    x = rows[fitted.names]
    for h in cfg.horizons:
        stack = np.sort(np.column_stack([np.asarray(fitted.models[h, q].predict(x)) for q in cfg.quantiles]), axis=1)
        stack = stack + rows["y"].to_numpy()[:, None]
        if widen and fitted.widening.get(h):
            stack[:, 0] -= fitted.widening[h]
            stack[:, -1] += fitted.widening[h]
        stack = np.maximum(stack, cfg.floor)
        for i, q in enumerate(cfg.quantiles):
            out[f"p{round(q * 100)}_{h}"] = stack[:, i]
    return out


def backtest(
    frame: Frame,
    config: QuantileConfig,
    test_years: list[int],
    *,
    origin_stride: int = 7,
    models_out: dict[int, FittedModels] | None = None,
) -> Frame:
    """One row per (test year, series, origin, horizon) with the actual value, the three quantiles and both
    baselines. Origins are every `origin_stride` days of the test year whose target date has a reading. With
    `models_out`, the models fitted for each test year are kept there (a caller can forecast from them).
    """
    rows: list[Frame] = []
    lo_name, hi_name = f"p{round(min(config.quantiles) * 100)}", f"p{round(max(config.quantiles) * 100)}"
    mid_name = "p50"
    for year in test_years:
        start, end = pd.Timestamp(year, 1, 1), pd.Timestamp(year + 1, 1, 1)
        fitted = fit(frame, config, target_before=start)
        if models_out is not None:
            models_out[year] = fitted
        clim = climatology(frame, start)
        test = frame[(frame["day"] >= start) & (frame["day"] < end) & frame["y"].notna()]
        test = test[(test["day"] - start).dt.days % origin_stride == 0]
        pred = predict(fitted, test)
        for h in config.horizons:
            actual = test[f"target_{h}"]
            keep = actual.notna()
            target_doy = (test["day"] + pd.Timedelta(days=h)).dt.dayofyear
            origin_doy = test["day"].dt.dayofyear
            climate = np.array([
                clim.at[d, s] if s in clim else np.nan for d, s in zip(target_doy, test["series"], strict=True)
            ])
            origin_climate = np.array([
                clim.at[d, s] if s in clim else np.nan for d, s in zip(origin_doy, test["series"], strict=True)
            ])
            rows.append(
                pd.DataFrame({
                    "year": year,
                    "series": test["series"],
                    "day": test["day"],
                    "horizon": h,
                    "actual": actual,
                    "persistence": test["y"],
                    "climatology": climate,
                    # a stronger reference than either required baseline: today's value plus the usual
                    # seasonal change between the origin's calendar day and the target's
                    "seasonal_drift": test["y"].to_numpy() + climate - origin_climate,
                    "lo": pred[f"{lo_name}_{h}"],
                    "p50": pred[f"{mid_name}_{h}"],
                    "hi": pred[f"{hi_name}_{h}"],
                })[keep]
            )
    return pd.concat(rows, ignore_index=True)


FEATURE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("recent trend", ("delta_",)),
    ("storage history", ("same_day_last_year", "max_365", "min_365", "y")),
    ("rainfall", ("precip_",)),
    ("temperature", ("temp_",)),
    ("season", ("doy_",)),
    ("which series", ("series_code",)),
)


def feature_group(name: str) -> str:
    """The readable group a feature belongs to (for SHAP summaries)."""
    for group, prefixes in FEATURE_GROUPS:
        if any(name == p or (p.endswith("_") and name.startswith(p)) for p in prefixes):
            return group
    return "other"


def explain(fitted: FittedModels, rows: Frame, horizon: int, quantile: float = 0.5) -> Frame:
    """LightGBM's own tree SHAP for the model of (`horizon`, `quantile`), summed into readable groups: how many
    percentage points of the predicted change each group of features contributes (a `base` column holds the
    model's average). Rows in, rows out; the sum of a row is the predicted change in storage.
    """
    contrib = np.asarray(fitted.models[horizon, quantile].predict(rows[fitted.names], pred_contrib=True))
    out = pd.DataFrame(0.0, index=rows.index, columns=[g for g, _ in FEATURE_GROUPS] + ["other", "base"])
    for i, name in enumerate(fitted.names):
        out[feature_group(name)] += contrib[:, i]
    out["base"] = contrib[:, -1]
    return out.loc[:, (out != 0).any(axis=0)]


def latest_forecast(frame: Frame, fitted: FittedModels) -> list[dict[str, Any]]:
    """For each series, the forecast made from its last observed day: p10/p50/p90 per horizon and what drives
    the median. This is what the dashboard and the assistant read; nothing is computed at request time.
    """
    last = frame[frame["y"].notna()].sort_values("day").groupby("series").tail(1)
    pred = predict(fitted, last)
    out: list[dict[str, Any]] = []
    for idx, row in last.iterrows():
        entry: dict[str, Any] = {
            "series": row["series"],
            "origin": str(row["day"].date()),
            "current_pct": round(row["y"], 2),
        }
        horizons: dict[str, Any] = {}
        for h in fitted.config.horizons:
            drivers = explain(fitted, last.loc[[idx]], h).iloc[0].round(2).to_dict()
            horizons[str(h)] = {
                **{
                    f"p{round(q * 100)}": round(float(pred.at[idx, f"p{round(q * 100)}_{h}"]), 2)
                    for q in fitted.config.quantiles
                },
                "drivers_pp": drivers,
            }
        entry["horizons"] = horizons
        out.append(entry)
    return sorted(out, key=lambda e: e["series"])


def summarise(results: Frame, nominal: float = 0.8) -> Frame:
    """MAE (percentage points) of the median and both baselines, and the central interval's coverage, per horizon."""
    g = results.assign(
        e_model=(results["p50"] - results["actual"]).abs(),
        e_persist=(results["persistence"] - results["actual"]).abs(),
        e_clim=(results["climatology"] - results["actual"]).abs(),
        e_drift=(results["seasonal_drift"] - results["actual"]).abs(),
        inside=(results["actual"] >= results["lo"]) & (results["actual"] <= results["hi"]),
        width=results["hi"] - results["lo"],
    )
    out = g.groupby("horizon").agg(
        n=("actual", "size"),
        mae_model=("e_model", "mean"),
        mae_persistence=("e_persist", "mean"),
        mae_climatology=("e_clim", "mean"),
        mae_seasonal_drift=("e_drift", "mean"),
        coverage=("inside", "mean"),
        mean_width=("width", "mean"),
    )
    out["nominal"] = nominal
    out["beats_persistence"] = out["mae_model"] < out["mae_persistence"]
    out["beats_climatology"] = out["mae_model"] < out["mae_climatology"]
    return out.round(3)
