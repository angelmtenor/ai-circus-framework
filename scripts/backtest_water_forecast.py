#!/usr/bin/env python3
"""Backtest the reservoir storage forecast and write its committed artifacts.

    PYTHONPATH=services/training/src:libs/shared/src services/training/.venv/bin/python scripts/backtest_water_forecast.py

Reads scenarios/water_reservoirs_drought/sample_data (the frozen snapshot, never a live API) and writes next to it
backtest.json, backtest_report.md and forecast.json. ~2 minutes on a laptop CPU. A run is mirrored to MLflow only
when MLFLOW_TRACKING_URI is set (best effort, never fails the script).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from training.core.quantile_forecast import QuantileConfig
from training.core.storage_report import markdown, run

SCENARIO = Path(__file__).resolve().parent.parent / "scenarios" / "water_reservoirs_drought"


def main() -> None:
    out = run(
        SCENARIO,
        QuantileConfig(horizons=(7, 30, 90, 180), conformal=True),
        tracking_uri=os.environ.get("MLFLOW_TRACKING_URI"),
    )
    (SCENARIO / "backtest.json").write_text(json.dumps(out["report"], indent=2) + "\n")
    (SCENARIO / "backtest_report.md").write_text(markdown(out["report"]))
    (SCENARIO / "forecast.json").write_text(json.dumps(out["forecast"], indent=2) + "\n")
    print(json.dumps(out["report"]["acceptance"], indent=2))


if __name__ == "__main__":
    main()
