# 0016 — Time-series forecasting is a third tabular task, not a new kind

Source: docs/adr/0016-time-series-forecasting.md (this repository)

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
  levers that shift exogenous features. Static SVG — no canvas, so no frame loop. Its second tile
  is the window's total for a flow (`summary: total`, the default) or, for a level such as storage
  %, the change from the las
