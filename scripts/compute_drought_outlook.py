#!/usr/bin/env python3
"""Compute the drought outlook of every supply system and write scenarios/water_reservoirs_drought/drought_outlook.json.

    PYTHONPATH=services/training/src:libs/shared/src services/training/.venv/bin/python scripts/compute_drought_outlook.py

Inputs are all committed: the rulebook, the cleaned observed storage (snapshot), and forecast.json (written by
scripts/backtest_water_forecast.py). The numbers come from ai_circus_shared.drought_outlook, the same function the
assistant's tools and the bulletin use.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_circus_shared.drought_outlook import outlook
from ai_circus_shared.rulebook import load
from training.core.storage_series import build_series

SCENARIO = Path(__file__).resolve().parent.parent / "scenarios" / "water_reservoirs_drought"


def main() -> None:
    book = load(SCENARIO / "rulebook" / "rulebook.yaml")
    table, notes = build_series(SCENARIO)
    forecast = json.loads((SCENARIO / "forecast.json").read_text())
    by_series = {s["series"]: s for s in forecast["series"]}
    out = []
    for system in notes["systems"]:
        rows = table[table["series"] == system].sort_values("day")
        observed = [(d.date(), None if v != v else float(v)) for d, v in zip(rows["day"], rows["y"], strict=True)]
        horizons = {
            int(h): {k: v for k, v in q.items() if k in ("p10", "p50", "p90")}
            for h, q in by_series[system]["horizons"].items()
        }
        out.append(outlook(book, system, observed, horizons))
    result = {
        "rulebook": book.id,
        "forecast_origin": forecast["origin"],
        "systems": out,
        "note": "Illustrative decision support on synthetic data. Not an operational tool.",
    }
    (SCENARIO / "drought_outlook.json").write_text(json.dumps(result, indent=2) + "\n")
    for s in out:
        rec = s["optimiser"]
        print(
            f"{s['system']:<20} {s['as_of']} {s['current_pct']:>6.1f} % {s['state']:<10} "
            f"(raw {s['raw_state']}, {s['days_in_state']} days) | dry-180 optimiser: {rec['chosen']} "
            f"(meets target: {rec['meets_target']})"
        )


if __name__ == "__main__":
    main()
