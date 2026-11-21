# 0016 — Time-series forecasting is a third tabular task, not a new kind

Source: docs/adr/0016-time-series-forecasting.md (this repository)

**Consequences.** One library enters training (statsmodels) and none enters prediction. A forecast
scenario has no per-row decision records, business rules, graph or text features, and no tutorial
yet — the schema rejects them. It has no drift reference either: a what-if's inputs are shifts of
assumed values, not observations; drift on newly ingested exogenous data is follow-up work.
etl-tabular never drops or samples rows of a series: it fails on a gap, a repeated timestamp or a
missing target, and keeps the most recent timestamps over the row cap. Forecasting the weather from the previous day is a stand-in, stated in the
scenario; a real deployment would feed a weather forecast as exogenous values.
