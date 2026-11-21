# Reservoirs & Drought Watch: what is built, how it works, what is not - How the pieces fit

Source: docs/scenarios/water_reservoirs_drought_guide.md (this repository)

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
