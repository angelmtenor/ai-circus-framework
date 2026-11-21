# 0016 — Time-series forecasting is a third tabular task, not a new kind

Source: docs/adr/0016-time-series-forecasting.md (this repository)

*"A scenario is one or the other" is superseded by [ADR 0018](0018-several-models-per-scenario.md): a model is.*

**Context.** Several tabular scenarios are really series: `energy_building` (a home's appliance
energy, sampled every 10 minutes), `surf_demand_forecast` (weekly sales per store × rider group,
with hand-made lag columns), `luznova_regional_demand` (daily load per province). Trained as
independent-row regressions they leak (a lag column built on the whole set), cannot say what
happens *next*, and give no honest uncertainty. The UI already guessed at a `tabular_ml_timeseries`
kind that nothing produced. A new service per task would cost ~200 MiB per pod on a ~12 GiB laptop.
