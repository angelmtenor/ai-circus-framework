"""The `lightgbm_quantile` forecast candidate and the water scenario's definition (docs/adr/0030)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from ai_circus_shared.scenario_schema import QUANTILE_CANDIDATE, ScenarioDefinition

SCENARIO = Path(__file__).resolve().parents[3] / "scenarios" / "water_reservoirs_drought" / "scenario.yaml"
pytestmark = pytest.mark.skipif(not SCENARIO.exists(), reason="scenario not present")


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return yaml.safe_load(SCENARIO.read_text())


def _build(raw: dict[str, Any], **changes: Any) -> ScenarioDefinition:
    data = copy.deepcopy(raw)
    for dotted, value in changes.items():
        node = data
        *path, last = dotted.split("__")
        for key in path:
            node = node[key]
        node[last] = value
    return ScenarioDefinition.model_validate(data)


def test_the_scenario_is_valid_and_uses_the_quantile_candidate(raw: dict[str, Any]) -> None:
    definition = _build(raw)
    assert definition.model is not None and definition.model.candidates == [QUANTILE_CANDIDATE]
    assert definition.time_series is not None and definition.time_series.horizons == [7, 30, 90, 180]
    assert definition.analysis_toolset == "drought_watch"
    assert definition.governance is not None and definition.governance.ai_act.risk_category == "limited"
    assert definition.governance.human_oversight.mode == "approve" and definition.governance.data.personal_data is False


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"time_series__horizons": [30, 7, 90, 180]}, "increasing order"),
        ({"time_series__horizons": [7, 30, 90]}, "end at time_series.horizon"),
        ({"time_series__horizons": [0, 180]}, "increasing order"),
        ({"time_series__frequency": "h"}, "frequency 'D'"),
        ({"time_series__horizons": []}, "required for"),
        ({"model__candidates": ["seasonal_naive"]}, "required for"),
        ({"model__candidates": ["lightgbm_quantile", "auto_ets"]}, "runs alone"),
        ({"time_series__prediction_intervals": [80, 95]}, "must be \\[80\\]"),
        ({"time_series__series_id_columns": []}, "exactly one column"),
        ({"time_series__max_rows": 10_000_000}, "less than or equal"),
        ({"analysis_toolset": "anything_else"}, "drought_watch"),
    ],
)
def test_the_rules_of_the_quantile_candidate(raw: dict[str, Any], changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _build(raw, **changes)


def test_the_horizons_field_does_nothing_for_the_other_candidates() -> None:
    from ai_circus_shared.scenario_schema import TimeSeriesConfig

    config = TimeSeriesConfig(time_column="t", frequency="h", horizon=24, lags=[1])
    assert config.horizons == [] and config.max_rows is None
