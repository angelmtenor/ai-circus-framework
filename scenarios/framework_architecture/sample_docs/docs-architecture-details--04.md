# Architecture details - Forecasts (`task_type: forecast`)

Source: docs/architecture-details.md (this repository)

A tabular scenario can be a **forecast** (`model.task_type: forecast` + `time_series:`, ADR 0016, first user
`energy_building`): etl-tabular's `clean_series` fails on gaps instead of dropping rows; training backtests
`seasonal_naive`/`auto_ets`/`lightgbm_lags` by MASE (features in `ai_circus_shared.forecasting`, shared with prediction so
they cannot skew; no Nixtla libraries, they pin `pandas<3`); prediction serves `GET /forecast/{slug}`, `POST
/forecast/{slug}/what-if` and `GET /model/{slug}/backtest`, and row-scoring routes answer 409; the assistant gets
`get_forecast`/`get_forecast_backtest`; `ForecastViews.tsx` replaces the model tab's Predictions/Insights and adds `ui_extras: forecast_board`.

**Several models in one scenario** (`extra_models:`, ADR 0018): each model is a validated view of the scenario, addressed as
`slug~id` (bare slug = main model), with its own dataset and bucket. etl-tabular, training and prediction loop over the views;
entitlement is to the scenario; the assistant gets every model's tools (an extra model's with `_<id>` appended); the UI has one
tab per model with a Predictions/Insights switch. A `risk_watchlist` can name two of its scenario's extra models in
`next_best_offer` (first user `insurance_portfolio`): `NextBestOfferPanel.tsx` scores the selected entity with the classifier
`propensity_model` (same feature columns, the schema checks it) and asks the recommender what goes with each product the
entity holds (`holdings`: 0/1 feature → item id), leaving out what it has. Both are calls the caller is already entitled to.

**Recommenders** (`model.task_type: recommendation` + `recommender:`, first user `surf_ml~recommender`): baskets in, no customers.
`training/core/recommender.py` scores `association_rules` and `item_factors` (truncated SVD) on items hidden from held-out
baskets, next to a popularity baseline (both rank by share of an item's baskets; ranking by lift or cosine found half as many hidden items); every candidate is served side by side by `GET /recommend/{slug}` and `/items`.
