# Reservoirs & Drought Watch: what is built, how it works, what is not - Forecast and backtest

Source: docs/scenarios/water_reservoirs_drought_guide.md (this repository)

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
