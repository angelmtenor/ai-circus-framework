# Backtest report: storage forecast

> Reference implementation on synthetic data. Not an operational tool.

Generated 2026-11-20 from the committed snapshot (2000-01-01 to 2026-11-20). Regenerate with `scripts/backtest_water_forecast.py`.

## Method

- Target: storage % of each reservoir and supply system at t+7, t+30, t+90, t+180 days.
- Model: LightGBM quantile regression (p10/p50/p90), one model per horizon and quantile, pooled over all series, predicting the *change* from today. Features are past-only: current %, lags 1/7/30/90/365, same date last year, 365-day range, catchment precipitation over 7/30/90/365 days, temperature anomaly, day of year.
- Rolling origin: for each test year 2010-2026, models are fit on origins whose *target date* is before that year and scored on every 7th day of it. Nothing from the test year is seen in fitting, calibration or the climatology.
- Interval calibration: split-conformal widening of the p10-p90 band, from a second model set fit one year earlier and scored on that last training year (`conformal=True`). The decision to calibrate was made after an uncalibrated run covered 65-77 % against a nominal 80 %; no other tuning was done on the test years, and LightGBM settings are the defaults in `quantile_forecast.py`.
- Baselines: persistence (today's value) and seasonal climatology (mean of the same calendar window in the years before the test year). *Seasonal drift* (today's value plus the usual seasonal change) is shown as a stronger reference but is not an acceptance bar.
- Cleaning applied before training: {'duplicates_dropped': 179, 'excluded': 333, 'imputed_days': 348} (see `scenarios/water_reservoirs_drought/config/quality.yaml`).

## All series

| Horizon (days) | Origins | MAE model (pp) | MAE persistence | MAE climatology | MAE seasonal drift | p10-p90 coverage |
|---|---|---|---|---|---|---|
| 7 | 11606 | **0.919** | 1.553 | 19.148 | 1.527 | 80.8% (nominal 80%) |
| 30 | 11560 | **4.167** | 5.627 | 19.198 | 5.076 | 81.4% (nominal 80%) |
| 90 | 11452 | **10.243** | 13.22 | 19.264 | 11.256 | 80.9% (nominal 80%) |
| 180 | 11283 | **16.504** | 18.69 | 19.296 | 16.585 | 75.2% (nominal 80%) |

## Supply systems only

| Horizon (days) | Origins | MAE model (pp) | MAE persistence | MAE climatology | MAE seasonal drift | p10-p90 coverage |
|---|---|---|---|---|---|---|
| 7 | 3568 | **0.908** | 1.592 | 18.52 | 1.561 | 80.6% (nominal 80%) |
| 30 | 3555 | **4.171** | 5.81 | 18.561 | 5.224 | 80.8% (nominal 80%) |
| 90 | 3521 | **10.114** | 13.666 | 18.629 | 11.545 | 80.2% (nominal 80%) |
| 180 | 3469 | **16.343** | 19.198 | 18.66 | 16.87 | 75.4% (nominal 80%) |

## Individual reservoirs only

| Horizon (days) | Origins | MAE model (pp) | MAE persistence | MAE climatology | MAE seasonal drift | p10-p90 coverage |
|---|---|---|---|---|---|---|
| 7 | 8038 | **0.924** | 1.536 | 19.427 | 1.512 | 80.9% (nominal 80%) |
| 30 | 8005 | **4.165** | 5.546 | 19.48 | 5.01 | 81.7% (nominal 80%) |
| 90 | 7931 | **10.3** | 13.022 | 19.546 | 11.127 | 81.1% (nominal 80%) |
| 180 | 7814 | **16.575** | 18.464 | 19.578 | 16.459 | 75.1% (nominal 80%) |

## central: drought years 2022-2024

| Horizon (days) | Origins | MAE model (pp) | MAE persistence | MAE climatology | MAE seasonal drift | p10-p90 coverage |
|---|---|---|---|---|---|---|
| 7 | 157 | **0.35** | 0.755 | 39.522 | 0.962 | 80.9% (nominal 80%) |
| 30 | 158 | **2.818** | 3.042 | 39.805 | 3.523 | 81.6% (nominal 80%) |
| 90 | 158 | **10.247** | 8.698 | 40.218 | 9.075 | 84.2% (nominal 80%) |
| 180 | 158 | **27.323** | 16.748 | 40.945 | 17.151 | 74.1% (nominal 80%) |

## north_coast: drought years 2022-2024

| Horizon (days) | Origins | MAE model (pp) | MAE persistence | MAE climatology | MAE seasonal drift | p10-p90 coverage |
|---|---|---|---|---|---|---|
| 7 | 159 | **0.399** | 0.929 | 39.379 | 1.283 | 83.6% (nominal 80%) |
| 30 | 159 | **2.954** | 3.74 | 39.595 | 4.846 | 76.1% (nominal 80%) |
| 90 | 159 | **12.444** | 10.487 | 39.451 | 11.548 | 81.1% (nominal 80%) |
| 180 | 159 | **31.221** | 18.609 | 40.668 | 19.34 | 69.8% (nominal 80%) |

## south_valley: drought years 2022-2024

| Horizon (days) | Origins | MAE model (pp) | MAE persistence | MAE climatology | MAE seasonal drift | p10-p90 coverage |
|---|---|---|---|---|---|---|
| 7 | 159 | **0.292** | 0.76 | 33.286 | 1.065 | 81.8% (nominal 80%) |
| 30 | 159 | **3.347** | 3.131 | 33.737 | 4.106 | 81.8% (nominal 80%) |
| 90 | 159 | **13.241** | 8.89 | 34.059 | 10.555 | 84.3% (nominal 80%) |
| 180 | 159 | **29.569** | 16.488 | 36.182 | 17.13 | 69.8% (nominal 80%) |

## westfield: drought years 2022-2024

| Horizon (days) | Origins | MAE model (pp) | MAE persistence | MAE climatology | MAE seasonal drift | p10-p90 coverage |
|---|---|---|---|---|---|---|
| 7 | 159 | **0.591** | 1.889 | 22.644 | 1.503 | 81.8% (nominal 80%) |
| 30 | 159 | **4.716** | 7.686 | 22.671 | 5.726 | 83.6% (nominal 80%) |
| 90 | 159 | **12.888** | 21.202 | 22.59 | 14.354 | 86.8% (nominal 80%) |
| 180 | 159 | **20.412** | 31.871 | 22.691 | 22.973 | 78.0% (nominal 80%) |

## Acceptance checks

- PASS: p50 beats both baselines at 30
- PASS: p50 beats both baselines at 90
- PASS: coverage within tolerance

## Reading these numbers

- Climatology is a weak baseline here: reservoir storage swings between years far more than within a season, so its error is about the spread of the series. Compare the model with persistence and with seasonal drift for a fairer reading.
- The pooled acceptance bars can hide weak spots. In the drought years, per system, the model was **not better than persistence** or its interval's coverage was **more than the tolerance away from nominal** (too narrow, or too wide) at:
  - central, 90 days: MAE 10.247 pp vs persistence 8.698 pp
  - central, 180 days: MAE 27.323 pp vs persistence 16.748 pp
  - north_coast, 90 days: MAE 12.444 pp vs persistence 10.487 pp
  - north_coast, 180 days: MAE 31.221 pp vs persistence 18.609 pp; coverage 70% vs nominal 80%
  - south_valley, 30 days: MAE 3.347 pp vs persistence 3.131 pp
  - south_valley, 90 days: MAE 13.241 pp vs persistence 8.89 pp
  - south_valley, 180 days: MAE 29.569 pp vs persistence 16.488 pp; coverage 70% vs nominal 80%
- Coverage is measured over all origins and series pooled; it can differ by series and year (see the per-year tables in `backtest.json`).
