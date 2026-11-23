# Reservoirs & Drought Watch: what is built, how it works, what is not - Status

Source: docs/scenarios/water_reservoirs_drought_guide.md (this repository)

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

<p align="center">
  <img src="../screenshots/water_reservoirs_drought/outlook.webp" alt="Outlook and measures for the South Valley system: with a dry 180 days, the bad, most likely and good cases, the chance of falling below each threshold, and the least restrictive package of measures that keeps the chance of emergency at or below 10 %" width="900">
</p>
