# Reservoirs & Drought Watch (Riverbend Water Authority, fictional)

> **Reference implementation on synthetic data. Not an operational tool.**

A water authority's most critical daily figure is how much water is stored and where it is heading. This scenario
turns a daily reservoir feed into an explained storage forecast on a map of the basin, a drought-state engine, a
what-if and measure comparison, a data-quality view, an assistant that answers ad-hoc questions from tools, and a weekly
bulletin. The authority, its basin, rivers, reservoirs and weather stations are fictional; all the data is synthetic;
the drought plan is fictional.

Implementation status, design and limits: [docs/scenarios/water_reservoirs_drought_guide.md](../../docs/scenarios/water_reservoirs_drought_guide.md).

## The data: a calibrated synthetic basin

`scripts/generate_water_data.py` simulates the Riverbend basin from `config/` (seeded, so the same config gives the
same files):

- **Weather:** 24 stations on the basin's own grid. Rain comes from one regional pattern (wet and dry spells cluster)
  with local variation, a dry-summer / wet-autumn climate, and chosen dry and wet years: two moderate droughts, a long
  severe one in 2022-2024 and wet recoveries. Temperature follows the season with persistent anomalies.
- **Reservoirs:** each catchment's rain and warmth feed a soil bucket whose runoff fills the reservoir; releases follow
  the season and fall as storage falls (the operator's response); evaporation and spills close the balance. Each
  reservoir's demand is calibrated to a long-run mean storage and its catchment to a yearly turnover
  (`config/systems.yaml`).
- **An unvalidated feed:** the readings are published with duplicate days, short gaps, frozen sensors, single spikes,
  readings just above 100 % and a stretch with a shifted level-to-volume table, so the data-quality view has something
  to flag. A flag means "needs review", never "wrong".

What this means for the numbers: the backtest shows the method (rolling origin by year, two baselines, a calibrated
uncertainty band, the drought years on their own), not accuracy on a real basin.

## What is here

| Path | What it is |
|---|---|
| `config/` | The generator's climate and dry years (`data_sources.yaml`), the supply systems and reservoirs with their positions and calibration targets (`systems.yaml`), data-quality thresholds and training policy (`quality.yaml`) |
| `sample_data/` | The generated snapshot (Parquet), `reservoirs.json`, `SNAPSHOT.json` (row counts, SHA-256, seed), `water.sqlite.gz` for the assistant's SQL tool, and `water_storage_daily.csv.gz`, the 13 series etl-tabular reads |
| `rulebook/` | `rulebook.yaml` is the source of truth; `plan_en.md` and `plan_es.md` are generated from it |
| `backtest_report.md`, `backtest.json` | Rolling-origin backtest against two baselines, with the weak spots listed |
| `forecast.json`, `drought_outlook.json` | The latest forecast per series, and the state, crossings, what-if and optimiser per system |
| `golden_prompts.yaml` | The seven demo prompts with the tools and reference plans a correct answer uses |
| `scenario.yaml` | The scenario definition: a `forecast` with the `lightgbm_quantile` candidate, the forecast board tab with its basin map, the `drought_watch` assistant toolset and the `governance:` block |

## Commands

Run from the repository root; the Python is each service's own environment.

```bash
# the synthetic snapshot (--check verifies the committed files against SNAPSHOT.json)
PYTHONPATH=libs/shared/src services/training/.venv/bin/python scripts/generate_water_data.py [--check]

# forecast + backtest report, then the outlook, then the assistant's SQLite file
export PYTHONPATH=services/training/src:libs/shared/src
services/training/.venv/bin/python scripts/backtest_water_forecast.py
services/training/.venv/bin/python scripts/compute_drought_outlook.py
services/training/.venv/bin/python scripts/build_water_database.py

# the series the pipeline reads (etl-tabular -> training -> prediction, like any forecast scenario)
services/training/.venv/bin/python scripts/prepare_water_storage_seed.py

# the rulebook's markdown plans (--check fails if they differ from rulebook.yaml)
libs/shared/.venv/bin/python scripts/generate_rulebook.py scenarios/water_reservoirs_drought/rulebook [--check]

# the ten-minute walkthrough, offline
services/assistant/.venv/bin/python scripts/demo_water_drought.py
```

Another basin with real data needs no code: the same file layout in `sample_data/` (readings, weather,
`reservoirs.json`) and its own `config/`; everything downstream reads only that layout.
