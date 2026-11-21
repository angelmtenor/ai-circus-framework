# 0030 — Reservoir drought watch: generic building blocks, scenario as config

Source: docs/adr/0030-reservoir-drought-watch-building-blocks.md (this repository)

Builds on ADR 0016 (time-series forecasting) and 0018 (several models per scenario). The scenario is described in
[docs/scenarios/water_reservoirs_drought_guide.md](../scenarios/water_reservoirs_drought_guide.md).

**Context.** A daily reservoir feed, published unvalidated, should become an explained forecast, a drought
state against plan thresholds, a what-if, a data-quality view, an assistant that never invents figures, and a weekly
bulletin. ADR 0016's forecast task is recursive, selects by MASE and has no quantile output, no multi-horizon direct
model, no baselines named in the acceptance bar and no notion of thresholds or hysteresis. The framework's SQL tool is
a `data_product` feature that needs the Data Platform tiers, which do not fit the 12 GB target.

**Decision.**
- **Reusable modules, not scenario code.** `libs/shared` gets the parts any daily series could use: a canonical observation
  shape (`open_data`, `geo`), data-quality checks with a documented training policy (`series_quality`), a threshold state
  machine with hysteresis and crossing times (`threshold_states`), a what-if and optimiser over quantile forecasts
  (`decision_support`), a rulebook generated from one YAML (`rulebook`), the outlook and bulletin built on them
  (`drought_outlook`, `drought_bulletin`), a read-only SQL guard (`readonly_sql`) and a check that figures in a text
  come from its sources (`traceability`). Everything is pure Python (libs/shared has no numpy). `training` gets
  `quantile_forecast`, `storage_series` and `storage_report`.
- **Direct multi-horizon quantile forecast** (LightGBM, one model per horizon and quantile, pooled over series,
  predicting the change from today), with a split-conformal band calibrated out of sample and a rolling-origin
  backtest against persistence and seasonal climatology. The report lists where it is weaker; the acceptance checks
  are pooled and say so.
- **Read-only SQL is enforced by SQLite, not by parsing**: a read-only open, an authorizer that allows only `SELECT`
  and the listed tables, one statement, a row cap and a time limit. A CTE that reuses the name of a hidden table is
  refused. It reads a gzipped SQLite file built from the snapshot (3 MB committed, unpacked once), which keeps the
  assistant free of pandas and a data tier.
- **One YAML is the rulebook.** The markdown plans (English, Spanish) are generated and a test fails on any
  difference; the state model, the measure packages and the citations all come from it.
- **The assistant's tools are a closed toolset** (`analysis_toolset: drought_watch` in the scenario schema), not a path
  or a module a scenario file names. The data is the same for every tenant; the entitlement check still gates the
  chat and the bulletin route.
- **The data is a calibrated synthetic basin.** `scripts/generate_water_data.py` simulates a fictional basin (weather
  stations, a soil-bucket runoff, operating rules, multi-year droughts, the faults of an unvalidated feed) from
  config, seeded. The repository names no real place, operator or data portal; the backtest shows the method, not
  accuracy on a real basin. A real basin needs only the same `sample_data/` layout.
- **The forecast board gets a schematic map** (`ui_extras.map`: lines and one site per series on a plain x/y plane,
  `series_labels`, `summary: change` for a level): generic, static SVG, and checked by the schema against the
  series options.
