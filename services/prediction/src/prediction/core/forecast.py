"""
- Title:    Forecast serving: the stored forecast, and what-if re-forecasts
- Author:   Angel Martinez-Tenor

A `model.task_type: forecast` scenario (docs/adr/0016-time-series-forecasting.md) is forecast by
training, which stores the result (forecast.json); serving it is a lookup. A what-if — the
exogenous features shifted (e.g. 5 °C colder) — re-forecasts with the stored LightGBM booster
through the same features training used (ai_circus_shared.forecasting), a few milliseconds. A
statistical winner (seasonal naive, exponential smoothing) does not read the exogenous features,
so its what-if is its stored forecast, flagged as unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
from ai_circus_shared.forecasting import (
    ForecastFeatures,
    apply_floor,
    interval_bands,
    recursive_forecast,
    shift_exogenous,
)
from lightgbm import Booster


class UnknownSeriesError(LookupError):
    """The requested series is not among the scenario's forecast series."""


@dataclass(frozen=True)
class Forecaster:
    """forecaster.json (lightgbm_lags only), loaded once with the model."""

    booster: Booster
    features: ForecastFeatures
    half_widths: dict[str, float]
    series: list[dict[str, Any]]
    floor: float | None = None

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> Forecaster:
        """Rebuild the booster from its stored text model."""
        return cls(
            booster=Booster(model_str=document["model"]),
            features=ForecastFeatures.model_validate(document["features"]),
            half_widths=document["half_widths"],
            series=document["series"],
            floor=document.get("floor"),
        )

    def forecast(self, index: int, shifts: dict[str, float]) -> tuple[list[float], dict[str, Any]]:
        """Series `index` re-forecast with its assumed exogenous values moved by `shifts`;
        returns the values and their bands.
        """
        stored = self.series[index]
        exogenous = shift_exogenous(stored["exogenous"], shifts)

        def predict_row(row: list[float]) -> float:
            return float(np.asarray(self.booster.predict(np.asarray([row], dtype=float)))[0])

        values = recursive_forecast(
            predict_row,
            self.features,
            stored["history"],
            [datetime.fromisoformat(t) for t in stored["times"]],
            exogenous,
            stored["static"],
        )
        values = [round(v, 4) for v in apply_floor(values, self.floor)]
        return values, interval_bands(values, self.half_widths, stored["scale"], self.floor)


def series_index(forecast: dict[str, Any], label: str | None) -> int:
    """Position of the series labelled `label` (None = the first)."""
    if label is None:
        return 0
    for index, entry in enumerate(forecast["series"]):
        if entry["label"] == label:
            return index
    raise UnknownSeriesError(f"No forecast series {label!r}.")


def what_if(
    forecast: dict[str, Any], forecaster: Forecaster | None, label: str | None, shifts: dict[str, float]
) -> tuple[dict[str, Any], bool]:
    """The series' forecast under `shifts`, and whether the model could re-forecast it (False =
    the selected model ignores exogenous features: the stored forecast, unchanged).
    """
    index = series_index(forecast, label)
    entry = dict(forecast["series"][index])
    if forecaster is None or not any(shifts.values()):
        return entry, forecaster is not None
    values, bands = forecaster.forecast(index, shifts)
    peak = max(range(len(values)), key=values.__getitem__)
    entry["forecast"] = {"ds": entry["forecast"]["ds"], "mean": values, "bands": bands}
    entry["exogenous"] = shift_exogenous(entry["exogenous"], shifts)
    entry["peak"] = {"ds": entry["forecast"]["ds"][peak], "value": values[peak]}
    return entry, True
