# 0030 — Reservoir drought watch: generic building blocks, scenario as config

Source: docs/adr/0030-reservoir-drought-watch-building-blocks.md (this repository)

- **The quantile model is a forecast candidate, not a new service path.** `lightgbm_quantile` in
  `model.candidates` (alone; it needs `time_series.horizons`, an 80 % band and one series id column, checked by the
  schema) makes `training` run `quantile_pipeline`, which returns ADR 0016's `ForecastRun`: the p50 path with the
  p10-p90 band as the 80 % level, a backtest beside persistence and seasonal climatology, and per-horizon MAE and
  coverage in the metadata. Training writes it like any forecast, so `prediction` serves it through its existing
  org-scoped routes unchanged. etl-tabular reads a gzipped seed and takes a per-scenario row cap
  (`time_series.max_rows`, at most 250,000) because a 13-series daily history exceeds the default 30,000.

**Consequences.** The scenario is registered and runs through the ordinary pipeline. The forecast is served from the
stored document (no what-if re-forecast on the board), and the assistant's drought tools still read the committed
artifacts in the scenario folder rather than the forecast trained for the caller's tenant. The dry/normal/wet scenarios
reuse the forecast's own quantiles (a conservative stress), and the savings follow an illustrative demand split in the
rulebook: both are simplifications, stated in the guide.
