"""
- Title:    The `lightgbm_quantile` forecast candidate: direct p10/p50/p90 at several horizons, in ForecastRun's shape
- Author:   Angel Martinez-Tenor

Runs `quantile_forecast` on a forecast scenario's cleaned series (etl-tabular's parquet) and returns the same
`ForecastRun` the recursive candidates produce, so training writes, signs and mirrors it exactly like them and
`prediction` serves it through the existing `/forecast` and `/model/.../backtest` routes (docs/adr/0030).

What it writes, in the existing documents:
- forecast: per series, the p50 path (joined linearly between the trained horizons) with the p10-p90 band as the
  80 % level, and the last observed history;
- backtest: the persistence and seasonal-climatology baselines next to the model (MASE, sMAPE, RMSE over a rolling
  origin by year), and, for the last three years, the forecast made on 1 January against what happened;
- metadata (`quantile_forecast`): MAE in percentage points and the band's coverage per horizon, and whether the median
  beat both baselines, so a run that does not is visible rather than silently shipped.

The scenario's two exogenous features are named by convention: `precip_mm` (rolling past rain) and `temp_c`
(temperature anomaly).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from ai_circus_shared.scenario_schema import ScenarioDefinition

from training.core.forecasting import HISTORY_TAIL_MAX, ForecastCandidate, ForecastRun, seasonal_scale
from training.core.logger import get_logger
from training.core.quantile_forecast import (
    FittedModels,
    Frame,
    QuantileConfig,
    backtest,
    climatology,
    explain,
    feature_frame,
    fit,
    latest_forecast,
    predict,
    summarise,
)

logger = get_logger(__name__)

SEASON = 7  # the scale of MASE: the in-sample error of a one-week-ago forecast
WINDOWS_STORED = 3  # backtest windows kept for the UI (each: the forecast made on 1 January)
SERIES_STORED = 12
IMPORTANCE_ROWS = 2_000
REQUIRED_EXOGENOUS = ("precip_mm", "temp_c")
NAME = "lightgbm_quantile"


def _table(df: Frame, definition: ScenarioDefinition) -> Frame:
    assert definition.time_series is not None and definition.dataset is not None
    ts, ds = definition.time_series, definition.dataset
    frame = df.reset_index() if df.index.name == ts.time_column else df
    missing = [c for c in REQUIRED_EXOGENOUS if c not in ts.exogenous_features]
    if missing:
        raise ValueError(f"{NAME} reads the exogenous features {list(REQUIRED_EXOGENOUS)}; missing {missing}.")
    return pd.DataFrame({
        "series": frame[ts.series_id_columns[0]].astype(str),
        "day": pd.to_datetime(frame[ts.time_column]),
        "y": frame[ds.target].astype(float),
        "precip_mm": frame["precip_mm"].astype(float),
        "temp_c": frame["temp_c"].astype(float),
    })


def daily_path(current: float, checkpoints: dict[int, float], horizon: int) -> list[float]:
    """Values for days 1..horizon: straight lines from today's value through the trained horizons."""
    xs = [0, *sorted(checkpoints)]
    ys = [current, *(checkpoints[h] for h in sorted(checkpoints))]
    return [float(v) for v in np.interp(np.arange(1, horizon + 1), xs, ys)]


def _metrics(results: Frame, column: str, scale: dict[str, float]) -> dict[str, float]:
    rows = results[np.isfinite(results[column])]
    err = rows[column] - rows["actual"]
    scaled = err.abs() / rows["series"].map(scale)
    denom = rows[column].abs() + rows["actual"].abs()
    smape = (200 * err.abs() / denom)[denom > 0]
    return {
        "mase": round(float(scaled.mean()), 4),
        "smape": round(float(smape.mean()) if len(smape) else 0.0, 4),
        "rmse": round(float(np.sqrt((err**2).mean())), 4),
    }


def _forecast_doc(
    frame: Frame, table: Frame, fitted: FittedModels, definition: ScenarioDefinition, id_column: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    assert definition.time_series is not None
    ts = definition.time_series
    floor = ts.target_min
    latest = latest_forecast(frame, fitted)
    series_docs: list[dict[str, Any]] = []
    for entry in latest:
        name = entry["series"]
        rows = table[table["series"] == name].sort_values("day")
        last = rows["day"].iloc[-1]
        times = [t.isoformat() for t in pd.date_range(last, periods=ts.horizon + 1, freq="D")[1:]]
        current = float(entry["current_pct"])
        paths = {
            q: daily_path(current, {int(h): v[q] for h, v in entry["horizons"].items()}, ts.horizon)
            for q in ("p10", "p50", "p90")
        }
        if floor is not None:
            paths = {q: [max(v, floor) for v in p] for q, p in paths.items()}
        mean = [round(v, 4) for v in paths["p50"]]
        peak = max(range(len(mean)), key=mean.__getitem__)
        tail = rows.tail(min(HISTORY_TAIL_MAX, max(4 * ts.horizon, 2 * SEASON)))
        series_docs.append({
            "key": {id_column: name},
            "label": name,
            "history": {"ds": [t.isoformat() for t in tail["day"]], "y": [round(float(v), 4) for v in tail["y"]]},
            "forecast": {
                "ds": times,
                "mean": mean,
                "bands": {
                    "80": {"lower": [round(v, 4) for v in paths["p10"]], "upper": [round(v, 4) for v in paths["p90"]]}
                },
            },
            "exogenous": [],
            "peak": {"ds": times[peak], "value": mean[peak]},
        })
    doc = {
        "model_name": NAME,
        "frequency": ts.frequency,
        "horizon": ts.horizon,
        "levels": ["80"],
        "series": series_docs,
    }
    return doc, latest


def _windows(
    frame: Frame, table: Frame, models: dict[int, FittedModels], years: list[int], definition: ScenarioDefinition,
    id_column: str,
) -> list[dict[str, Any]]:  # fmt: skip
    """For the last years: the forecast made on 1 January (from models that never saw the year) against what
    happened over the next `horizon` days, next to the two baselines.
    """
    assert definition.time_series is not None
    ts = definition.time_series
    out: list[dict[str, Any]] = []
    for year in years[-WINDOWS_STORED:]:
        origin = pd.Timestamp(year, 1, 1) - pd.Timedelta(days=1)
        rows = frame[frame["day"] == origin]
        if rows.empty:
            continue
        fitted = models[year]
        pred = predict(fitted, rows)
        clim = climatology(frame, pd.Timestamp(year, 1, 1))
        entries = []
        for idx, row in list(rows.iterrows())[:SERIES_STORED]:
            name = row["series"]
            actual_rows = table[(table["series"] == name) & (table["day"] > origin)].sort_values("day").head(ts.horizon)
            if len(actual_rows) < ts.horizon:
                continue
            days = list(actual_rows["day"])
            checkpoints = {h: float(pred.at[idx, f"p50_{h}"]) for h in fitted.config.horizons}
            median = daily_path(float(row["y"]), checkpoints, ts.horizon)
            climate = [float(clim.at[d.dayofyear, name]) for d in days]
            entries.append({
                "key": {id_column: name},
                "label": name,
                "ds": [d.isoformat() for d in days],
                "actual": [round(float(v), 4) for v in actual_rows["y"]],
                "forecasts": {
                    "persistence": [round(float(row["y"]), 4)] * ts.horizon,
                    "seasonal_climatology": [round(v, 4) for v in climate],
                    NAME: [round(v, 4) for v in median],
                },
            })
        if entries:
            out.append({"cutoff": origin.isoformat(), "series": entries})
    return out


def _importance(frame: Frame, fitted: FittedModels, horizon: int) -> list[dict[str, Any]]:
    sample = frame[frame["y"].notna()].tail(IMPORTANCE_ROWS)
    contrib = explain(fitted, sample, horizon).drop(columns=["base"], errors="ignore").abs().mean()
    return [{"feature": k, "importance": round(float(v), 4)} for k, v in contrib.sort_values(ascending=False).items()]


def run_quantile_forecast(
    df: Frame, definition: ScenarioDefinition, *, rounds: int | None = None, stride: int | None = None
) -> ForecastRun:
    """Backtest by year, fit on everything and forecast ahead; see the module docstring. `rounds` and `stride` lower
    the boosting rounds and the training-origin stride (a test or a quick look); the defaults are the production ones.
    """
    assert definition.time_series is not None and definition.dataset is not None and definition.model is not None
    ts = definition.time_series
    id_column = ts.series_id_columns[0]
    table = _table(df, definition)
    overrides = {k: v for k, v in (("rounds", rounds), ("stride", stride)) if v is not None}
    config = QuantileConfig(
        horizons=tuple(ts.horizons),
        conformal=True,
        floor=ts.target_min or 0.0,
        **overrides,  # type: ignore[arg-type]
    )
    frame = feature_frame(table, config.horizons)
    last_day = frame.loc[frame["y"].notna(), "day"].max()
    years = list(range(int(last_day.year) - ts.backtest.windows + 1, int(last_day.year) + 1))
    models: dict[int, FittedModels] = {}
    results = backtest(frame, config, years, models_out=models)
    logger.info("Quantile backtest over {} years: {} rows", len(years), len(results))

    scale = {str(s): seasonal_scale(list(g["y"].dropna()), SEASON) for s, g in table.groupby("series")}
    candidates = [
        ForecastCandidate(name, _metrics(results, column, scale))
        for name, column in (("persistence", "persistence"), ("seasonal_climatology", "climatology"), (NAME, "p50"))
    ]
    selected = candidates[-1]
    summary = summarise(results)
    required = [h for h in (30, 90) if h in summary.index]
    beaten = {int(h): bool(summary.at[h, "beats_persistence"] and summary.at[h, "beats_climatology"]) for h in required}
    if not all(beaten.values()):
        logger.warning("{} did not beat both baselines at {}", NAME, [h for h, ok in beaten.items() if not ok])

    fitted = fit(frame, config, target_before=last_day + pd.Timedelta(days=1))
    forecast_doc, _ = _forecast_doc(frame, table, fitted, definition, id_column)
    windows = _windows(frame, table, models, years, definition, id_column)
    backtest_doc = {
        "selection_metric": definition.model.selection_metric or "mase",
        "selected": NAME,
        "horizon": ts.horizon,
        "candidates": [{"name": c.name, "metrics": c.metrics} for c in candidates],
        "windows": windows,
    }
    middle = config.horizons[len(config.horizons) // 2]
    extra = {
        "quantile_forecast": {
            "horizons": list(config.horizons),
            "quantiles": list(config.quantiles),
            "backtest_years": [years[0], years[-1]],
            "p50_beats_both_baselines": {str(h): ok for h, ok in beaten.items()},
            "by_horizon": {
                str(h): {
                    "n": int(row["n"]),
                    "mae_pp": float(row["mae_model"]),
                    "mae_persistence_pp": float(row["mae_persistence"]),
                    "mae_climatology_pp": float(row["mae_climatology"]),
                    "coverage_p10_p90": float(row["coverage"]),
                }
                for h, row in summary.iterrows()
            },
            "note": "Interval widened by a split-conformal step calibrated one year out of sample. The p50 path "
            "between trained horizons is a straight line.",
        }
    }
    train_rows = int(frame[f"target_{middle}"].notna().sum())
    return ForecastRun(
        candidates, selected, forecast_doc, backtest_doc, None, _importance(frame, fitted, middle), train_rows, extra
    )
