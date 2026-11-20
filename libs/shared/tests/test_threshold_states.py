"""ai_circus_shared.threshold_states — bands, hysteresis, and when a forecast crosses a threshold."""

from __future__ import annotations

import pytest

from ai_circus_shared.threshold_states import (
    Band,
    StateModel,
    crossing_days,
    crossing_range,
    probability_below,
    run_states,
)

THREE = StateModel(bands=[Band(id="A", min_value=60), Band(id="B", min_value=40), Band(id="C")])


def test_bounds_are_inclusive_below_and_validated() -> None:
    assert [THREE.classify(v) for v in (100, 60, 59.99, 40, 39.99, 0, -5)] == ["A", "A", "B", "B", "C", "C", "C"]
    assert THREE.lower_bound("B") == 40 and THREE.lower_bound("C") is None
    with pytest.raises(ValueError, match="strictly decreasing"):
        StateModel(bands=[Band(id="A", min_value=40), Band(id="B", min_value=60), Band(id="C")])
    with pytest.raises(ValueError, match="last must have none"):
        StateModel(bands=[Band(id="A", min_value=60), Band(id="B", min_value=40)])
    with pytest.raises(ValueError, match="unique"):
        StateModel(bands=[Band(id="A", min_value=60), Band(id="A")])


def test_getting_worse_is_immediate_and_can_skip_states() -> None:
    assert run_states([70, 30], THREE, 7) == ["A", "C"]


def test_getting_better_waits_for_n_consecutive_better_days() -> None:
    assert run_states([70, 55, 55, 70, 70, 70], THREE, 3) == ["A", "B", "B", "B", "B", "A"]


def test_a_relapse_resets_the_run() -> None:
    states = run_states([70, 55, 70, 70, 55, 70, 70, 70], THREE, 3)
    assert states == ["A", "B", "B", "B", "B", "B", "B", "A"]


def test_improvement_lands_on_the_worst_state_of_the_run() -> None:
    # in C, then A, B, A: three better days, the worst of which is B
    assert run_states([30, 70, 50, 70], THREE, 3) == ["C", "C", "C", "B"]


def test_each_further_improvement_needs_a_new_run() -> None:
    states = run_states([30, 50, 50, 50, 70, 70, 70], THREE, 3)
    assert states == ["C", "C", "C", "B", "B", "B", "A"]


def test_missing_days_hold_the_state_and_do_not_count() -> None:
    assert run_states([70, 55, None, 70, None, 70, 70], THREE, 3) == ["A", "B", "B", "B", "B", "B", "A"]
    assert run_states([None, None, 55], THREE, 3) == [None, None, "B"]


def test_one_day_of_hysteresis_means_no_hysteresis() -> None:
    assert run_states([70, 55, 70], THREE, 1) == ["A", "B", "A"]


def test_staying_in_the_same_state_never_changes_it() -> None:
    assert run_states([55] * 10, THREE, 3) == ["B"] * 10


def test_crossing_interpolates_between_horizons() -> None:
    path = [(0.0, 50.0), (30.0, 40.0), (90.0, 30.0)]
    assert crossing_days(path, 35) == pytest.approx(60.0)
    assert crossing_days(path, 45) == pytest.approx(15.0)
    assert crossing_days(path, 20) is None
    assert crossing_days(path, 60) == 0.0  # already below


def test_crossing_range_orders_pessimistic_before_median_before_optimistic() -> None:
    forecast = {
        30: {"p10": 38.0, "p50": 44.0, "p90": 50.0},
        90: {"p10": 25.0, "p50": 36.0, "p90": 47.0},
    }
    r = crossing_range(52.0, forecast, 40.0)
    assert r.pessimistic is not None and r.median is not None
    assert r.pessimistic < r.median
    assert r.optimistic is None  # the p90 path stays above 40 through the whole horizon
    assert crossing_range(52.0, forecast, 47.5).optimistic is not None
    assert crossing_range(52.0, forecast, 10.0).median is None  # not within 90 days


def test_probability_from_quantiles() -> None:
    q = {0.1: 40.0, 0.5: 55.0, 0.9: 66.0}
    assert probability_below(q, 55) == pytest.approx(0.5)
    assert probability_below(q, 40) == pytest.approx(0.1)
    assert probability_below(q, 47.5) == pytest.approx(0.3)
    assert probability_below(q, 1000) == 0.99 and probability_below(q, -1000) == 0.01
    assert probability_below(q, 30) < probability_below(q, 45) < probability_below(q, 60)
    with pytest.raises(ValueError):
        probability_below({0.5: 1.0}, 1.0)
