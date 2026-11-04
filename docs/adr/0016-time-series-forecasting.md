# 0016 — Time-series forecasting is a third tabular task, not a new kind

**Context.** Several tabular scenarios are really series: `energy_building` (a home's appliance
energy, sampled every 10 minutes), `surf_demand_forecast` (weekly sales per store × rider group,
with hand-made lag columns), `luznova_regional_demand` (daily load per province). Trained as
independent-row regressions they leak (a lag column built on the whole set), cannot say what
happens *next*, and give no honest uncertainty. The UI already guessed at a `tabular_ml_timeseries`
kind that nothing produced. A new service per task would cost ~200 MiB per pod on a ~12 GiB laptop.

**Decision.**
- **`model.task_type: forecast`** next to classification and regression, on the same `tabular_ml`
  kind and services (etl-tabular, training, prediction, assistant). A forecast scenario carries a
  **`time_series:`** block — time column, series id columns (none = one series), pandas
  frequency, horizon, lags, calendar features, rolling windows, static and exogenous features,
  how future exogenous values are assumed (`seasonal_naive`: same hour/day of the previous
  season; `last_value`), the backtest (rolling-origin windows) and the prediction-interval levels.
  `dataset.feature_columns` must equal series ids + static + exogenous: lags and calendar features
  are built by the library at train and predict time, never stored, so they cannot leak.
- **Candidates, simplest first:** `seasonal_naive`, `auto_ets` (statsmodels' exponential
  smoothing, the configuration with the lowest AIC) and `lightgbm_lags` (LightGBM on lags,
  rolling statistics, calendar and exogenous features, forecast recursively). Nixtla's
  mlforecast/statsforecast were the first choice but pin `pandas<3`, and every service here runs
  pandas 3; the features are a small pure-Python module instead (`ai_circus_shared.forecasting`),
  shared by training and prediction so the two can never build them differently. Selection by
  rolling-origin backtest **MASE** (`smape`/`rmse` selectable) under the existing Green Code rule —
  the more complex model wins only when it beats the simpler one by
  `accuracy_gain_threshold_for_complexity`. The winner is refit on all data; intervals are
  conformal (from the backtest residuals), so they are calibrated for every candidate alike, and
  forecasts and bands stop at the target's physical minimum (`time_series.target_min`). MASE's
  scale is in-sample seasonal-naive error, so unseen-data values above 1 are normal: the
  `seasonal_naive` candidate's score is the baseline a reader compares with.
- **Artifacts** (checksummed and signed like the row models' and listed in `metadata.json`):
  `forecast.json` (per series: history tail, point forecast, interval bands, assumed exogenous
  values, peak), `backtest.json` (per window and candidate: actual vs forecast, metrics) and, for
  `lightgbm_lags`, `forecaster.json` (the feature layout and the booster as text — JSON, not
  pickle). Explanations are LightGBM's own tree SHAP grouped back to what a reader recognises:
  *recent history* (lags, rolling), *calendar*, and each exogenous/static feature by name.
- **Serving:** prediction adds `GET /forecast/{slug}`, `POST /forecast/{slug}/what-if` (shift the
  assumed exogenous values — only exogenous features, within the board's lever range) and
  `GET /model/{slug}/backtest`, entitled and org-scoped like every route. Routes that score
  independent rows (`/predict`, dataset evaluation and explainability) answer 409 for a forecast
  scenario; the model card works unchanged (`test_score` is the backtest MASE). The assistant gets
  `get_forecast` (with optional shifts) and `get_forecast_backtest` instead of the row tools, and a
  forecast-specific prompt. statsmodels is **training-only**; prediction re-forecasts with the
  LightGBM booster it already has, and a statistical winner is served from its stored forecast
  (its what-if is the stored forecast: it does not read the exogenous features).
- **UI:** the gallery's "Time Series" subtype and technique key on `task_type === "forecast"` (the
  phantom kind is gone). Predict and ML Insights get forecast variants, and a new
  **`forecast_board`** tab shows history + forecast + 80/95 % bands, the backtest, the peak and
  levers that shift exogenous features. Static SVG — no canvas, so no frame loop.
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
