# Reservoirs & Drought Watch: what is built, how it works, what is not

> **Reference implementation on synthetic data. Not an operational tool.**

The scenario's files are in [`scenarios/water_reservoirs_drought/`](../../scenarios/water_reservoirs_drought/README.md),
with how the synthetic basin is generated. This page says what exists, how the pieces fit, the simplifications made, and
what is left.

## Status

The scenario is **registered** as an ordinary `tabular_ml` forecast (`scenario.yaml`): `platform-registry` seeds it,
the pipeline runs it like any forecast scenario (etl-tabular reads `sample_data/water_storage_daily.csv.gz`, training
runs the `lightgbm_quantile` candidate and writes the tenant's `forecast.json`/`backtest.json`, `prediction` serves them
through its existing org-scoped `/forecast` and `/model/.../backtest` routes), and the assistant adds the eight drought
tools to its chat (`analysis_toolset: drought_watch`). The same toolset turns the `forecast_board` tab into the **drought
control room** (`DroughtControlRoom.tsx`): each supply system's state against the plan, the basin map coloured by storage
band with a weekly replay since 2000, the forecast with the plan's thresholds behind it, the what-if and the measure
optimiser, what drives the forecast, the data-quality flags (readings as published next to the cleaned series), the plan
and the weekly bulletin. It reads the assistant's read-only `/drought/{slug}/…` routes, which call the very toolkit the
chat's tools call, so the tab and the assistant cannot disagree. The Overview tab tells the story and runs a ten-minute
guided demo (`story:`). The tools, the control room and the bulletin use the forecast **trained for the caller's
organisation** when `prediction` serves one from the same readings (same last day and current storage: the tools'
history, flags and SQL are the scenario's committed data): they ask its org-scoped `/forecast` with the caller's own
token, as the chart does, and recompute the outlook from it. Otherwise they use the committed `forecast.json` and
`drought_outlook.json`; the room says which under the chart. Every quantile is clipped to 0-100 % of capacity in the
tools, the bulletin and the chart alike. One limit remains: the forecast board has **no what-if re-forecast** for this
model (it is served from the stored forecast, like a statistical winner).

| Part | State | Where |
|---|---|---|
| Data: calibrated synthetic basin, seeded, with an unvalidated feed's faults | Built; the banner is in every generated text and on the board's subtitle | `scripts/generate_water_data.py`, `config/`, `sample_data/` |
| Domain model: reservoirs, systems, observations, system storage, drought state | Built as config and tables | `config/systems.yaml`, `water.sqlite.gz` |
| Rulebook: markdown plan + YAML twin, EN and ES | Built; one source of truth, generated both ways, drift-tested | `rulebook/`, `ai_circus_shared.rulebook` |
| Map | The forecast board's schematic map (`ui_extras.map`); in the control room each reservoir is coloured by its storage band on the day shown, with a weekly replay since 2000 | `ForecastViews.tsx`, `DroughtControlRoom.tsx` |
| Data & BI tab | The framework's generic tab; the assistant's SQL tool covers ad-hoc charts | |
| Data quality | Built: five checks, score, documented training policy; the control room lists every reservoir's flags and shows a window as published next to the cleaned series | `ai_circus_shared.series_quality`, `dq_flags.parquet`, `GET /drought/{slug}/quality` |
| Forecast, backtest, SHAP | Built; trained by the pipeline as the `lightgbm_quantile` candidate and served by `prediction`'s existing routes (p10-p90 as the 80 % band; drivers in the assistant's `forecast` tool and in the control room's "Why the forecast moves") | `training/core/quantile_forecast.py`, `quantile_pipeline.py`, `backtest_report.md` |
| Drought-state engine, days to threshold | Built (hysteresis, probability ranges) | `ai_circus_shared.threshold_states`, `drought_outlook` |
| What-if and optimiser | Built, with the simplifications below | `ai_circus_shared.decision_support` |
| Assistant tools (8) | Built and tested without a model; wired into the scenario's chat; not yet evaluated against a live model | `assistant/core/drought_tools.py` |
| Weekly bulletin, markdown and PDF | Built; PDF served by the assistant's `GET /bulletin/{slug}` | `ai_circus_shared.drought_bulletin`, `assistant/core/bulletin_pdf.py` |
| Governance block | Built, in the framework's schema | `scenario.yaml` (`governance:`) |
| Demo flow | Built as an offline walkthrough | `scripts/demo_water_drought.py` |

## How the pieces fit

```
config/ ──generate──> sample_data/*.parquet (seeded, SHA-256 in SNAPSHOT.json)
                                 │
          series_quality.check ──┴─> dq_flags.parquet, quality score, cleaned training copy
                                 │
        storage_series.build_series  (cleaned % per reservoir and per system, catchment rain and temperature)
                                 │
        quantile_forecast (LightGBM p10/p50/p90 at 7/30/90/180 days) ──> forecast.json, backtest.json, backtest_report.md
                                 │
 rulebook.yaml ──> threshold_states + decision_support ──> drought_outlook.json
        │                                                      │
        └─> plan_en.md, plan_es.md                             ├─> assistant tools (forecast, what_if, optimise_measures,
                                                               │     drought_state, search_rulebook, dq_report, run_sql)
                                                               └─> drought_bulletin ──> markdown ──> PDF
```

Reusable pieces live in `libs/shared` (observation shape, geo, data quality, state machine, decision support, rulebook, bulletin,
read-only SQL, traceability) and in `training` (`quantile_forecast`, `storage_series`, `storage_report`); the
scenario-specific parts are config files, the generator and the small `drought_tools` toolset. A basin with real data
needs the same `sample_data/` layout and no code.

## Forecast and backtest

LightGBM quantile regression, one model per horizon and quantile, pooled over the 13 series (9 reservoirs, 4 systems),
predicting the change from today. Features are past-only; a test proves altering the future leaves them unchanged. The
p10-p90 band is widened by a split-conformal step calibrated one year out of sample. The rolling-origin backtest runs
by test year (2010 to 2026) and compares with persistence and seasonal climatology; seasonal drift is shown as a
stronger reference. The committed [backtest report](../../scenarios/water_reservoirs_drought/backtest_report.md) is the
source of the numbers; read its list of weak spots before trusting a single system or a drought-year 180-day
forecast. The pooled acceptance checks (median beats both baselines at 30 and 90 days; coverage within 10 points of
nominal at 7, 30 and 90) pass; per system in the 2022-2024 drought they do not all hold, and the report says where.

The data is synthetic, so these numbers show the method, not accuracy on a real basin: the model learns the
generator's water balance. The generator is calibrated (climate, mean storage, turnover, drought depth) so the series
behave like real storage, and it never lets a forecast see a year it is tested on.

## Simplifications, and why

- **Scenarios use the model's own quantiles.** A direct model cannot take future rain as a feature, so "dry/normal/wet"
  moves the forecast distribution by the p10 or p90 distance from the median. The dry stress is conservative. A milder
  definition is a one-function change.
- **Savings come from the rulebook's illustrative demand split**, not estimated from historical dry-period rates.
- **Rulebook search is lexical and in-process**, not a vector search: the plan has about 40 paragraphs.
- **The fictional tenant is content, not a Keycloak organisation:** the scenario is seeded for the `admin` tenant like
  every scenario; a Riverbend organisation with its own entitlement is a deployment step, not code.
- **Other languages** are answered by the assistant (it translates); the plan and bulletin exist in English and Spanish.
- **The map is schematic:** a plain x/y grid of a fictional basin, drawn as SVG, not a projected map of a real place.

## What is left

1. ~~**Assistant on the tenant's artifacts.**~~ Built: the drought tools, the control room and the bulletin use the
   forecast trained for the caller's organisation and recompute the outlook from it (`DroughtToolkit.with_forecast`).
   Still open, if wanted: a what-if re-forecast for the quantile model on the board.
2. ~~**UI:** the data-quality view, the drought state on the map, and the forecast's drivers on the board.~~ Built in
   the drought control room.
3. **A live evaluation** of the golden prompts against a real model, using `untraceable()` on the answers.

## Running it

See the scenario README for the commands. `scripts/demo_water_drought.py` walks the ten-minute demo offline.
