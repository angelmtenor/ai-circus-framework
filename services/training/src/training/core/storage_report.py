"""
- Title:    Storage forecast report: run the backtest, fit the final models, write the committed artifacts
- Author:   Angel Martinez-Tenor

Turns the snapshot into `backtest.json` / `backtest_report.md` (rolling-origin backtest by year against the two
baselines, MAE in percentage points, interval coverage, and the drought years the scenario names on their own) and
`forecast.json`
(the latest p10/p50/p90 per series and horizon with the groups of features that drive the median). The numbers in
the markdown come from the same table as the JSON, so the two cannot disagree. Deterministic for a given snapshot
(LightGBM is seeded, two threads).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from training.core.mlflow_tracking import log_report_run
from training.core.quantile_forecast import (
    Frame,
    QuantileConfig,
    backtest,
    feature_frame,
    fit,
    latest_forecast,
    summarise,
)
from training.core.storage_series import build_series

BANNER = "Reference implementation on synthetic data. Not an operational tool."
FIRST_TEST_YEAR = 2010
BARS = {"beat_baselines_at": (30, 90), "coverage_tolerance_pp": 10}
# 180 days is optional: it is trained, backtested and shown (with wide bands) but is not an acceptance bar.
REQUIRED_HORIZONS = (7, 30, 90)


def _records(summary: Frame) -> list[dict[str, Any]]:
    return json.loads(str(summary.reset_index().to_json(orient="records")))


def _by(results: Frame, mask: Frame) -> list[dict[str, Any]]:
    return _records(summarise(results[mask]))


def run(scenario_dir: Path, config: QuantileConfig, *, tracking_uri: str | None = None) -> dict[str, Any]:
    """Backtest, fit the final models and return `report`, `forecast` and the raw `results` (nothing is written)."""
    table, notes = build_series(scenario_dir)
    frame = feature_frame(table, config.horizons)
    last_day = frame.loc[frame["y"].notna(), "day"].max()
    last_year = int(last_day.year)
    years = list(range(FIRST_TEST_YEAR, last_year + 1))
    results = backtest(frame, config, years)

    systems = set(notes["systems"])
    is_system = results["series"].isin(systems)
    # The drought the report looks at on its own: `drought_years: [first, last]` in config/data_sources.yaml.
    sources = yaml.safe_load((scenario_dir / "config" / "data_sources.yaml").read_text())
    first, last = sources.get("drought_years") or (0, -1)
    in_drought = results["year"].between(first, last)
    overall = summarise(results)
    tolerance = BARS["coverage_tolerance_pp"] / 100
    checks = {
        f"p50_beats_both_baselines_at_{h}": bool(
            overall.at[h, "beats_persistence"] and overall.at[h, "beats_climatology"]
        )
        for h in BARS["beat_baselines_at"]
        if h in overall.index
    }
    required = overall.loc[[h for h in REQUIRED_HORIZONS if h in overall.index]]
    checks["coverage_within_tolerance"] = bool(((required["coverage"] - required["nominal"]).abs() <= tolerance).all())

    weak: list[str] = []
    for system in sorted(systems):
        for row in _by(results, in_drought & (results["series"] == system)):
            problems = []
            if row["mae_model"] >= row["mae_persistence"]:
                problems.append(f"MAE {row['mae_model']} pp vs persistence {row['mae_persistence']} pp")
            if abs(row["coverage"] - row["nominal"]) > tolerance:
                problems.append(f"coverage {row['coverage']:.0%} vs nominal {row['nominal']:.0%}")
            if problems:
                weak.append(f"{system}, {row['horizon']} days: " + "; ".join(problems))

    fitted = fit(frame, config, target_before=last_day + pd.Timedelta(days=1))
    report: dict[str, Any] = {
        "drought_weak_spots": weak,
        "generated": datetime.now(UTC).date().isoformat(),
        "data_range": [str(frame["day"].min().date()), str(last_day.date())],
        "test_years": [years[0], years[-1]],
        "config": {
            "horizons": list(config.horizons),
            "quantiles": list(config.quantiles),
            "rounds": config.rounds,
            "stride": config.stride,
            "conformal": config.conformal,
            "lightgbm": config.params,
        },
        "cleaning_policy_applied": notes["cleaning"],
        "overall": _records(overall),
        "systems": _by(results, is_system),
        "reservoirs": _by(results, ~is_system),
        "drought_years": [first, last] if first <= last else None,
        "drought": {s: _by(results, in_drought & (results["series"] == s)) for s in sorted(systems)}
        if first <= last
        else {},
        "per_year_systems": {str(y): _by(results, is_system & (results["year"] == y)) for y in years},
        "acceptance": checks,
    }
    report["forecast_origin"] = str(last_day.date())
    forecast = {
        "generated": report["generated"],
        "origin": str(last_day.date()),
        "horizons": list(config.horizons),
        "series": latest_forecast(frame, fitted),
        "note": "Illustrative decision support on synthetic data. Not an operational tool.",
    }
    recorded = log_report_run(
        tracking_uri,
        scenario_slug="water_reservoirs_drought",
        run_name=f"quantile-backtest/{report['generated']}",
        params={
            "horizons": ",".join(map(str, config.horizons)),
            "conformal": config.conformal,
            "rounds": config.rounds,
        },
        metrics={
            f"{name}_{int(row['horizon'])}": float(row[name])
            for row in overall.reset_index().to_dict("records")
            for name in ("mae_model", "mae_persistence", "mae_climatology", "coverage")
        },
        report=report,
    )
    report["tracked_in_mlflow"] = recorded
    return {"report": report, "forecast": forecast, "results": results}


def markdown(report: dict[str, Any]) -> str:
    """The backtest report as markdown, from the same numbers as `backtest.json`."""

    def table(rows: list[dict[str, Any]]) -> str:
        head = (
            "| Horizon (days) | Origins | MAE model (pp) | MAE persistence | MAE climatology "
            "| MAE seasonal drift | p10-p90 coverage |"
        )
        lines = [head, "|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(
                f"| {r['horizon']} | {r['n']} | **{r['mae_model']}** | {r['mae_persistence']} | {r['mae_climatology']} "
                f"| {r['mae_seasonal_drift']} | {r['coverage']:.1%} (nominal {r['nominal']:.0%}) |"
            )
        return "\n".join(lines)

    cfg = report["config"]
    acc = report["acceptance"]
    out = [
        "# Backtest report: storage forecast",
        "",
        f"> {BANNER}",
        "",
        f"Generated {report['generated']} from the committed snapshot ({report['data_range'][0]} to "
        f"{report['data_range'][1]}). Regenerate with `scripts/backtest_water_forecast.py`.",
        "",
        "## Method",
        "",
        f"- Target: storage % of each reservoir and supply system at t+{', t+'.join(map(str, cfg['horizons']))} days.",
        "- Model: LightGBM quantile regression (p10/p50/p90), one model per horizon and quantile, pooled over all "
        "series, predicting the *change* from today. Features are past-only: current %, lags 1/7/30/90/365, "
        "same date last year, 365-day range, catchment precipitation over 7/30/90/365 days, temperature anomaly, "
        "day of year.",
        f"- Rolling origin: for each test year {report['test_years'][0]}-{report['test_years'][1]}, models are fit on "
        "origins whose *target date* is before that year and scored on every 7th day of it. Nothing from the test "
        "year is seen in fitting, calibration or the climatology.",
        f"- Interval calibration: split-conformal widening of the p10-p90 band, from a second model set fit one year "
        f"earlier and scored on that last training year (`conformal={cfg['conformal']}`). The decision to calibrate "
        "was made after an uncalibrated run covered 65-77 % against a nominal 80 %; no other tuning was done on the "
        "test years, and LightGBM settings are the defaults in `quantile_forecast.py`.",
        "- Baselines: persistence (today's value) and seasonal climatology (mean of the same calendar window in the "
        "years before the test year). *Seasonal drift* (today's value plus the usual seasonal change) is shown as a "
        "stronger reference but is not an acceptance bar.",
        f"- Cleaning applied before training: {report['cleaning_policy_applied']} (see "
        "`scenarios/water_reservoirs_drought/config/quality.yaml`).",
        "",
        "## All series",
        "",
        table(report["overall"]),
        "",
        "## Supply systems only",
        "",
        table(report["systems"]),
        "",
        "## Individual reservoirs only",
        "",
        table(report["reservoirs"]),
        "",
    ]
    years = report.get("drought_years") or []
    for system, rows in report.get("drought", {}).items():
        out += [f"## {system}: drought years {'-'.join(map(str, years))}", "", table(rows), ""]
    out += [
        "## Acceptance checks",
        "",
        *[f"- {'PASS' if ok else 'FAIL'}: {name.replace('_', ' ')}" for name, ok in acc.items()],
        "",
        "## Reading these numbers",
        "",
        "- Climatology is a weak baseline here: reservoir storage swings between years far more than within a "
        "season, so its error is about the spread of the series. Compare the model with persistence and with "
        "seasonal drift for a fairer reading.",
        "- The pooled acceptance bars can hide weak spots. In the drought years, per system, the model was "
        "**not better than persistence** or its interval's coverage was **more than the tolerance away from "
        "nominal** (too narrow, or too wide) at:",
        *([f"  - {w}" for w in report["drought_weak_spots"]] or ["  - (none)"]),
        "- Coverage is measured over all origins and series pooled; it can differ by series and year (see the "
        "per-year tables in `backtest.json`).",
        "",
    ]
    return "\n".join(out)
