# 0016 — Time-series forecasting is a third tabular task, not a new kind

Source: docs/adr/0016-time-series-forecasting.md (this repository)

  %, the change from the last observation (`summary: change`); with no levers it shows only the
  series picker.
- **First series: `energy_building`.** Its committed file is a random sample of the 10-minute
  data, so it is re-seeded hourly (`scripts/prepare_energy_building_hourly.py`: 3,289 hours, 40
  interpolated) under a new raw object key. `surf_demand_forecast` stays a regression for now:
  the surf_retail data product's Insights board and the assistant's `ml_predict` depend on its
  `/predict` contract. `luznova_regional_demand` stays one too, because its map scores any area
  under any conditions, whole-region aggregates included; `luznova_regional_forecast` is the same
  data as 50 series. A scenario is one or the other: a forecast cannot keep a tab that scores rows.

**Consequences.** One library enters training (statsmodels) and none enters prediction. A forecast
scenario has no per-row decision records, business rules, graph or text features, and no tutorial
yet — the schema rejects them. It has no drift reference either: a what-if's inputs are shifts of
assumed values, not observations; drift on newly ingested exogenous data is follow-up work.
etl-tabular never drops or samples rows of a series: it fails on a gap, a repeated timestamp or a
missing target, and keeps the most recent timestamps over the row cap. Forecasting the weather from the previous day is a stand-in, stated in the
scenario; a real deployment would feed a weather forecast as exogenous values.
