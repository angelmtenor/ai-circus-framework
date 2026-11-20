"""ai_circus_shared.decision_support — scenarios, savings, the probability of crossing, and the optimiser."""

from __future__ import annotations

import pytest

from ai_circus_shared.decision_support import (
    Package,
    apply_savings,
    apply_scenario,
    evaluate,
    optimise,
    quantiles_at,
)

CURRENT = 50.0
FORECAST = {
    30: {"p10": 38.0, "p50": 44.0, "p90": 52.0},
    90: {"p10": 20.0, "p50": 33.0, "p90": 48.0},
    180: {"p10": 10.0, "p50": 24.0, "p90": 45.0},
}
PACKAGES = [
    Package(id="none", label="None", savings=0.0),
    Package(id="alert", label="Alert", savings=0.14),
    Package(id="exceptional", label="Exceptional", savings=0.30),
    Package(id="emergency", label="Emergency", savings=0.47),
]


def test_scenario_moves_the_distribution_by_the_p10_or_p90_distance() -> None:
    dry = apply_scenario(FORECAST, "dry")
    assert dry[30] == {"p10": 32.0, "p50": 38.0, "p90": 46.0}  # all moved by p10 - p50 = -6
    assert apply_scenario(FORECAST, "normal") == FORECAST
    assert apply_scenario(FORECAST, "wet")[30]["p50"] == 52.0


def test_a_ceiling_stops_a_wet_scenario_above_full() -> None:
    wet = apply_scenario(FORECAST, "wet", ceiling=50.0)
    assert max(v for q in wet.values() for v in q.values()) == 50.0
    assert apply_scenario(FORECAST, "wet")[30]["p90"] == 60.0  # 52 + (52 - 44): unbounded without it


def test_savings_only_reduce_a_drawdown() -> None:
    out = apply_savings(CURRENT, FORECAST, 0.5)
    assert out[30]["p50"] == pytest.approx(47.0)  # 50 - 6 * 0.5
    assert out[30]["p90"] == 52.0  # a rise is untouched
    assert apply_savings(CURRENT, FORECAST, 0.0) == FORECAST
    with pytest.raises(ValueError):
        apply_savings(CURRENT, FORECAST, 1.5)


def test_quantiles_at_interpolates_between_checkpoints_and_from_today() -> None:
    mid = quantiles_at(FORECAST, 60)
    assert mid["p50"] == pytest.approx(38.5)
    early = quantiles_at(FORECAST, 15, current=CURRENT)
    assert early["p50"] == pytest.approx(47.0)
    with pytest.raises(ValueError, match="beyond"):
        quantiles_at(FORECAST, 200)
    with pytest.raises(ValueError, match="current value"):
        quantiles_at(FORECAST, 15)


def test_more_savings_never_raise_the_probability() -> None:
    probs = [
        evaluate(CURRENT, FORECAST, package=p, scenario="dry", horizon=180, threshold=15).probability_below
        for p in PACKAGES
    ]
    assert probs == sorted(probs, reverse=True)
    assert probs[0] > probs[-1]


def test_a_wetter_scenario_is_never_worse() -> None:
    by = {
        s: evaluate(CURRENT, FORECAST, package=PACKAGES[0], scenario=s, horizon=90, threshold=30).probability_below
        for s in ("dry", "normal", "wet")
    }
    assert by["dry"] >= by["normal"] >= by["wet"]


def test_horizon_is_respected_and_can_fall_between_checkpoints() -> None:
    short = evaluate(CURRENT, FORECAST, package=PACKAGES[0], scenario="dry", horizon=30, threshold=15)
    long = evaluate(CURRENT, FORECAST, package=PACKAGES[0], scenario="dry", horizon=180, threshold=15)
    assert short.probability_below <= long.probability_below
    between = evaluate(CURRENT, FORECAST, package=PACKAGES[0], scenario="normal", horizon=120, threshold=15)
    assert between.horizon == 120 and set(between.final) == {"p10", "p50", "p90"}


def test_optimise_picks_the_least_restrictive_package_that_meets_the_target() -> None:
    rec = optimise(CURRENT, FORECAST, PACKAGES, scenario="dry", horizon=180, threshold=15, target=0.10)
    assert rec.meets_target and rec.chosen is not None
    chosen = next(i for i, o in enumerate(rec.outcomes) if o.package == rec.chosen)
    assert rec.outcomes[chosen].probability_below <= 0.10
    assert all(o.probability_below > 0.10 for o in rec.outcomes[:chosen])  # nothing lighter would do
    assert rec.chosen in rec.note


def test_optimise_says_so_when_nothing_works() -> None:
    rec = optimise(CURRENT, FORECAST, PACKAGES, scenario="dry", horizon=180, threshold=40, target=0.05)
    assert not rec.meets_target and rec.chosen is None
    assert "No package" in rec.note and "emergency" in rec.note


def test_no_measures_are_needed_when_the_risk_is_already_small() -> None:
    rec = optimise(CURRENT, FORECAST, PACKAGES, scenario="wet", horizon=90, threshold=15, target=0.10)
    assert rec.chosen == "none"


def test_packages_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="least to most restrictive"):
        optimise(CURRENT, FORECAST, PACKAGES[::-1], scenario="dry", horizon=90, threshold=15)
