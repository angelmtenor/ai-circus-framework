"""Forecast serving (docs/adr/0016-time-series-forecasting.md): stored forecast, what-if, backtest, 409s."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import lightgbm as lgb
import numpy as np
import pytest
from ai_circus_shared.auth import Identity
from ai_circus_shared.forecasting import (
    MODEL_BACKTEST_KEY,
    MODEL_FORECAST_KEY,
    MODEL_FORECASTER_KEY,
    ForecastFeatures,
)
from ai_circus_shared.tabular_ml import MODEL_CHECKSUMS_METADATA_FIELD, MODEL_METADATA_KEY, artifact_checksum
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prediction.api import _model_cache, _scenario_definition, router
from prediction.app import _not_a_row_model_handler
from prediction.core.forecast import Forecaster, UnknownSeriesError, series_index, what_if
from prediction.core.identity import resolve_identity
from prediction.core.model_cache import ModelArtifacts, ModelCache, NotARowModelError

FEATURES = ForecastFeatures(lags=[1], exogenous=["temp"])
START = datetime(2026, 1, 1)
TIMES = [(START + timedelta(hours=h)).isoformat() for h in range(1, 4)]


def _booster() -> lgb.Booster:
    """Y = 2·temp + lag_1/10, so shifting temp must move the forecast."""
    rng = np.random.default_rng(0)
    x = np.column_stack([rng.uniform(0, 100, 400), rng.uniform(-10, 30, 400)])
    y = 2 * x[:, 1] + x[:, 0] / 10
    data = lgb.Dataset(x, y, feature_name=FEATURES.names())
    return lgb.train({"objective": "regression", "verbosity": -1, "seed": 0}, data, num_boost_round=50)


def _documents() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    exogenous = [{"temp": 10.0}] * 3
    forecast = {
        "model_name": "lightgbm_lags",
        "frequency": "h",
        "horizon": 3,
        "levels": ["80"],
        "series": [
            {
                "key": {},
                "label": "all",
                "history": {"ds": [START.isoformat()], "y": [50.0]},
                "forecast": {
                    "ds": TIMES,
                    "mean": [25.0, 22.5, 22.2],
                    "bands": {"80": {"lower": [0] * 3, "upper": [1] * 3}},
                },
                "exogenous": exogenous,
                "peak": {"ds": TIMES[0], "value": 25.0},
            }
        ],
    }
    forecaster = {
        "features": FEATURES.model_dump(),
        "model": _booster().model_to_string(),
        "half_widths": {"80": 0.5},
        "series": [{"key": {}, "history": [50.0], "times": TIMES, "exogenous": exogenous, "static": {}, "scale": 2.0}],
    }
    backtest = {
        "selection_metric": "mase",
        "selected": "lightgbm_lags",
        "horizon": 3,
        "candidates": [
            {"name": "seasonal_naive", "metrics": {"mase": 1.2}},
            {"name": "lightgbm_lags", "metrics": {"mase": 0.8}},
        ],
        "windows": [
            {
                "cutoff": START.isoformat(),
                "series": [
                    {
                        "key": {},
                        "label": "all",
                        "ds": TIMES,
                        "actual": [1, 2, 3],
                        "forecasts": {"seasonal_naive": [1, 1, 1]},
                    }
                ],
            }
        ],
    }
    return forecast, forecaster, backtest


def test_what_if_re_forecasts_with_the_booster_and_bands_follow() -> None:
    forecast, forecaster_doc, _ = _documents()
    forecaster = Forecaster.from_document(forecaster_doc)
    base, re_forecast = what_if(forecast, forecaster, None, {})
    assert re_forecast and base == forecast["series"][0]  # no shift: the stored forecast
    colder, _ = what_if(forecast, forecaster, "all", {"temp": -8.0})
    assert all(
        c < b - 5 for c, b in zip(colder["forecast"]["mean"], forecast["series"][0]["forecast"]["mean"], strict=True)
    )
    assert colder["exogenous"][0] == {"temp": 2.0}
    lower, upper = colder["forecast"]["bands"]["80"]["lower"], colder["forecast"]["bands"]["80"]["upper"]
    assert [round(u - lo, 4) for lo, u in zip(lower, upper, strict=True)] == [2.0] * 3  # ±0.5 x scale 2
    assert colder["peak"]["value"] == max(colder["forecast"]["mean"])


def test_what_if_without_a_forecaster_is_the_stored_forecast() -> None:
    forecast, _, _ = _documents()
    entry, re_forecast = what_if(forecast, None, None, {"temp": 3.0})
    assert not re_forecast and entry == forecast["series"][0]
    with pytest.raises(UnknownSeriesError):
        series_index(forecast, "nope")


class _Store:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def get(self, org_id: str, key: str) -> bytes:
        return self.objects[key]

    def exists(self, org_id: str, key: str) -> bool:
        return key in self.objects


def test_model_cache_loads_a_forecast_and_refuses_row_scoring() -> None:
    forecast, forecaster, backtest = _documents()
    objects = {
        MODEL_FORECAST_KEY: json.dumps(forecast).encode(),
        MODEL_BACKTEST_KEY: json.dumps(backtest).encode(),
        MODEL_FORECASTER_KEY: json.dumps(forecaster).encode(),
    }
    checksums = {
        "forecast": artifact_checksum(objects[MODEL_FORECAST_KEY]),
        "backtest": artifact_checksum(objects[MODEL_BACKTEST_KEY]),
        "forecaster": artifact_checksum(objects[MODEL_FORECASTER_KEY]),
    }
    metadata = {"scenario_slug": "energy", "task_type": "forecast", MODEL_CHECKSUMS_METADATA_FIELD: checksums}
    objects[MODEL_METADATA_KEY] = json.dumps(metadata).encode()
    cache = ModelCache({"energy": _Store(objects)}, fallback_org_id="demo")  # type: ignore[dict-item]
    artifacts = cache.get("org-1", "energy")
    assert artifacts.forecast == forecast and artifacts.backtest == backtest
    assert artifacts.forecaster is not None and artifacts.pipeline is None
    with pytest.raises(NotARowModelError, match="is a forecast"):
        artifacts.row_pipeline()


@pytest.fixture
def client() -> Generator[TestClient]:
    forecast, forecaster, backtest = _documents()
    artifacts = ModelArtifacts(
        pipeline=None,
        explainer=None,
        metadata={"scenario_slug": "energy", "task_type": "forecast", "feature_columns": ["temp"]},
        forecast=forecast,
        backtest=backtest,
        forecaster=Forecaster.from_document(forecaster),
    )

    class FakeModelCache(ModelCache):
        def __init__(self) -> None:
            pass

        def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
            return artifacts

    lever = SimpleNamespace(feature="temp", min_shift=-8.0, max_shift=8.0)
    definition = SimpleNamespace(
        slug="energy",
        dataset=SimpleNamespace(text_columns=list, feature_schema={}, business_rules=None),
        model=SimpleNamespace(decision_policy=None),
        time_series=SimpleNamespace(exogenous_features=["temp"]),
        ui_extras=SimpleNamespace(levers=[lever]),
    )
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(NotARowModelError, _not_a_row_model_handler)  # type: ignore[arg-type]
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="org-1", roles=frozenset())
    app.dependency_overrides[_scenario_definition] = lambda: definition
    cache = FakeModelCache()
    app.dependency_overrides[_model_cache] = lambda: cache
    yield TestClient(app)


def test_forecast_endpoints(client: TestClient) -> None:
    body = client.get("/forecast/energy").json()
    assert body["what_if"] is True and body["series"][0]["label"] == "all"
    assert client.get("/forecast/energy", params={"series": "nope"}).status_code == 404
    backtest = client.get("/model/energy/backtest").json()
    assert backtest["selected"] == "lightgbm_lags" and len(backtest["windows"]) == 1

    shifted = client.post("/forecast/energy/what-if", json={"shifts": {"temp": -5}})
    assert shifted.status_code == 200 and shifted.json()["re_forecast"] is True
    assert shifted.json()["series"]["forecast"]["mean"] != body["series"][0]["forecast"]["mean"]
    assert client.post("/forecast/energy/what-if", json={"shifts": {"humidity": 1}}).status_code == 422
    assert client.post("/forecast/energy/what-if", json={"shifts": {"temp": 20}}).status_code == 422


def test_row_scoring_routes_answer_409_for_a_forecast(client: TestClient) -> None:
    response = client.post("/predict/energy", json={"records": [{"temp": 3.0}]})
    assert response.status_code == 409 and "forecast" in response.json()["detail"]
