#!/usr/bin/env python3
"""A terminal walkthrough of the Reservoirs & Drought Watch demo (about ten minutes when narrated).

    services/assistant/.venv/bin/python scripts/demo_water_drought.py [--out DIR] [--lang en|es]

It runs the same code the assistant's tools run, over the frozen snapshot, so it needs no cluster and no network.
Six steps follow the scenario's demo flow; the basin map is on the forecast board. The weekly
bulletin is written to DIR as markdown and PDF (default: a temporary folder).
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import yaml
from assistant.core.bulletin_pdf import render
from assistant.core.drought_tools import DroughtToolkit

SCENARIO = Path(__file__).resolve().parent.parent / "scenarios" / "water_reservoirs_drought"


def step(number: int, title: str, minutes: str) -> None:
    print(f"\n{'=' * 78}\nSTEP {number}: {title}   [{minutes}]\n{'=' * 78}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None, help="where to write the bulletin (default: a temp folder)")
    parser.add_argument("--lang", default="en", choices=["en", "es"])
    args = parser.parse_args()
    out = args.out or Path(tempfile.mkdtemp(prefix="drought-demo-"))
    out.mkdir(parents=True, exist_ok=True)
    t = DroughtToolkit(SCENARIO)

    print("Reference implementation on synthetic data. Not an operational tool.")
    print(f"Riverbend Water Authority (fictional). Latest published readings: {t.as_of}.")

    step(1, "The state of every supply system  (the board's basin map shows the same)", "1 min")
    for sid, o in t.outlooks.items():
        members = ", ".join(t.reservoir_names[r] for r in t.members[sid])
        print(f"  {t.system_names[sid]:<26} {o['current_pct']:>5.1f} %  {o['state']:<10} "
              f"(7 days {o['change_7d_pp']:+.1f} pp)   reservoirs: {members}")  # fmt: skip

    step(2, "History and data quality: 'the feed publishes unvalidated readings; we flag, we do not hide'", "1.5 min")
    meta = json.loads((SCENARIO / "sample_data" / "reservoirs.json").read_text())
    for rid, m in sorted(meta.items(), key=lambda kv: kv[1]["dq_score"]):
        print(f"  {m['name']:<18} quality score {m['dq_score']:>5.1f} %")
    lake = t.dq_report("Lantern Lake")
    print(f"\n  Lantern Lake, last 30 days: {lake['days_flagged']} days need review {lake['flags_by_code']}")
    for code in lake["flags_by_code"]:
        print(f"  Why ({code}): {lake['meaning'][code]}.")
    older = t.dq_report("Lantern Lake", t.as_of.replace(year=t.as_of.year - 1), t.as_of)
    print(f"  Lantern Lake, last 12 months: {older['days_flagged']} days need review {older['flags_by_code']}")
    print(f"  Why ({'pct_range'}): {older['meaning']['pct_range']}.")

    step(3, "Forecast with bands, what drives it, and the backtest against baselines", "2 min")
    f = t.forecast_of("central")
    for h, q in f["horizons_days"].items():
        drivers = {k: v for k, v in q["drivers_pp"].items() if k != "base"}
        top = max(drivers, key=lambda k: abs(drivers[k]))
        print(f"  {h:>3} days: p50 {q['p50']:.1f} %  (p10 {q['p10']:.1f} - p90 {q['p90']:.1f}), "
              f"main driver: {top} {drivers[top]:+.1f} pp, typical error {f['typical_error_pp'][h]} pp")  # fmt: skip
    first, last = t.backtest["test_years"]
    print(f"\n  Backtest {first}-{last}, supply systems (mean absolute error in percentage points):")
    for row in t.backtest["systems"]:
        print(f"    {row['horizon']:>3} days: model {row['mae_model']:>6}  persistence {row['mae_persistence']:>6}  "
              f"climatology {row['mae_climatology']:>6}  coverage {row['coverage']:.0%} (nominal 80 %)")  # fmt: skip
    print("\n  Where it is weaker (drought years {}-{}):".format(*t.backtest["drought_years"]))
    for line in t.backtest["drought_weak_spots"]:
        print(f"    - {line}")

    step(4, "What-if: a dry quarter, and the least restrictive package", "2 min")
    for sid in ("central", "south_valley"):
        for scenario in ("normal", "dry"):
            w = t.what_if(sid, scenario, 0, 90)
            row = w["thresholds"][0]
            print(f"  {t.system_names[sid]:<26} {scenario:<6} 90 days: p50 ends {w['end_of_horizon_pct']['p50']:.1f} %; "
                  f"{row['state_below']} threshold ({row['threshold_pct']:g} %) reached: "
                  f"p10 {row['days_pessimistic_p10']} / p50 {row['days_median_p50']} / p90 {row['days_optimistic_p90']} days")  # fmt: skip
        rec = t.optimise_measures(sid, 10, 180, "dry")
        print(f"  -> dry, 180 days, P(emergency) <= 10 %: {rec['least_restrictive_package']}")
        print("     " + " | ".join(f"{p['package']} {p['probability_below_pct']}%" for p in rec["packages"]))
    print("  (Illustrative decision support: the dry scenario is a conservative stress, a person decides.)")

    step(5, "The assistant's tools: an ad-hoc question, a rulebook citation, then the bulletin", "3 min")
    sql = (
        "SELECT r.name, ROUND(e.pct - s.pct, 2) AS change_pp FROM reservoirs r "
        "JOIN storage_daily e ON e.series = r.id AND e.day = (SELECT max(day) FROM storage_daily) "
        "JOIN storage_daily s ON s.series = r.id AND s.day = date((SELECT max(day) FROM storage_daily), '-30 day') "
        "ORDER BY change_pp ASC LIMIT 3"
    )
    print("  'Which three reservoirs lost most storage in the last 30 days?'  -> run_sql, then render_chart")
    for name, change in t.run_sql(sql)["rows"]:
        print(f"    {name:<18} {change:+.2f} pp")
    print("\n  'What measures apply in exceptional state for agriculture?'  -> search_rulebook")
    hit = t.search_rulebook("agriculture measures in exceptional state")["results"][0]
    print(f"    {hit['text']}  ({hit['citation']})")
    bulletin = t.bulletin(None, args.lang)
    md = out / f"bulletin_{bulletin['week_of']}_{args.lang}.md"
    md.write_text(bulletin["markdown"], encoding="utf-8")
    pdf = md.with_suffix(".pdf")
    pdf.write_bytes(render(bulletin["markdown"], title=f"Drought bulletin {bulletin['week_of']}"))
    print(f"\n  'Draft this week's drought bulletin.'  -> generate_bulletin\n    {md}\n    {pdf}")

    step(6, "Declared risk, governed from the first call", "0.5 min")
    gov = yaml.safe_load((SCENARIO / "scenario.yaml").read_text())["governance"]
    print(f"  AI system: {gov['ai_system_id']}   owner: {gov['owner']}")
    print(f"  AI Act risk category (declared by the owner): {gov['ai_act']['risk_category']}")
    print(f"  Human oversight: {gov['human_oversight']['mode']} - personal data: {gov['data']['personal_data']}")
    print("  Traces and the model-call audit need the cluster: not part of this offline walkthrough.")


if __name__ == "__main__":
    main()
