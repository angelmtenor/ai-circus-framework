# Reservoirs & Drought Watch: what is built, how it works, what is not - Status

Source: docs/scenarios/water_reservoirs_drought_guide.md (this repository)

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
