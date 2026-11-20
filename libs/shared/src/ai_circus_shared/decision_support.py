"""What-if and measure optimisation over a forecast given as quantiles. Illustrative decision support.

Inputs are what a quantile forecast already says: for each horizon in days, a few quantiles of the indicator
(storage %). Two levers act on it:

- a **rainfall scenario** (`dry`, `normal`, `wet`) moves the whole forecast distribution by the distance between
  its median and its p10 / p90: a conservative stress, not a re-run of the model;
- **savings** (a share between 0 and 1) apply to the *net drawdown only*: where a quantile ends below today's value
  the fall is multiplied by (1 - savings); a rise is left as it is.

A **package** is a named set of measures with the savings it is estimated to give. `optimise` walks the packages
from the least to the most restrictive and returns the first whose probability of ending below a threshold at any
checkpoint up to the horizon is within a target. That probability is the largest over the forecast's checkpoints
(plus the horizon itself), a lower bound of "at any time", close to it for a steady drawdown.

Nothing here is a hydrological model, and nothing decides: a person reads the result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_circus_shared.threshold_states import CrossingRange, crossing_range, probability_below

Scenario = Literal["dry", "normal", "wet"]
Forecast = Mapping[int, Mapping[str, float]]  # horizon in days -> {"p10": .., "p50": .., "p90": ..}
QUANTILE_LEVELS = {"p10": 0.1, "p50": 0.5, "p90": 0.9}


class Package(BaseModel):
    """A named bundle of measures and its estimated demand-side savings (a share of the net drawdown)."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    savings: float = Field(ge=0.0, le=1.0)


def apply_scenario(
    forecast: Forecast, scenario: Scenario, *, ceiling: float | None = None
) -> dict[int, dict[str, float]]:
    """The forecast distribution moved to the scenario: `dry` by (p10 - p50), `wet` by (p90 - p50). With a
    `ceiling` (a reservoir cannot hold more than full) no quantile ends above it."""
    out: dict[int, dict[str, float]] = {}
    for h, q in forecast.items():
        shift = {"dry": q["p10"] - q["p50"], "normal": 0.0, "wet": q["p90"] - q["p50"]}[scenario]
        out[h] = {
            name: min(value + shift, ceiling) if ceiling is not None else value + shift for name, value in q.items()
        }
    return out


def apply_savings(current: float, forecast: Forecast, savings: float) -> dict[int, dict[str, float]]:
    """Reduce every quantile's fall below `current` by `savings` (a rise is untouched)."""
    if not 0.0 <= savings <= 1.0:
        raise ValueError("savings must be between 0 and 1")
    return {
        h: {n: (current + (v - current) * (1 - savings) if v < current else v) for n, v in q.items()}
        for h, q in forecast.items()
    }


def quantiles_at(forecast: Forecast, horizon: int, current: float | None = None) -> dict[str, float]:
    """Quantiles at an arbitrary horizon: linear between checkpoints (and from `current` at day 0)."""
    points = sorted(forecast)
    if horizon > points[-1]:
        raise ValueError(f"horizon {horizon} is beyond the forecast ({points[-1]} days)")
    if horizon in forecast:
        return dict(forecast[horizon])
    lo = max((h for h in points if h < horizon), default=None)
    hi = min(h for h in points if h > horizon)
    left = forecast[lo] if lo is not None else dict.fromkeys(forecast[hi], current) if current is not None else None
    if left is None:
        raise ValueError("a horizon before the first checkpoint needs the current value")
    lo_h = lo if lo is not None else 0
    t = (horizon - lo_h) / (hi - lo_h)
    return {n: left[n] + (forecast[hi][n] - left[n]) * t for n in forecast[hi]}


class Outcome(BaseModel):
    """What one package, in one scenario, does to the forecast."""

    package: str
    savings: float
    scenario: Scenario
    horizon: int
    threshold: float
    probability_below: float  # largest over the checkpoints up to `horizon`
    worst_checkpoint: int
    final: dict[str, float]  # the quantiles at `horizon`
    crossing: CrossingRange


def evaluate(
    current: float,
    forecast: Forecast,
    *,
    package: Package,
    scenario: Scenario,
    horizon: int,
    threshold: float,
    ceiling: float | None = None,
) -> Outcome:
    """Scenario, then savings, then the probability of being below `threshold` and when each path crosses it."""
    staged = apply_savings(current, apply_scenario(forecast, scenario, ceiling=ceiling), package.savings)
    checkpoints = sorted({h for h in staged if h <= horizon} | {horizon})
    at = {h: quantiles_at(staged, h, current) for h in checkpoints}
    per_checkpoint = {
        h: probability_below({QUANTILE_LEVELS[n]: v for n, v in q.items() if n in QUANTILE_LEVELS}, threshold)
        for h, q in at.items()
    }
    worst = max(per_checkpoint, key=lambda h: (per_checkpoint[h], -h))
    return Outcome(
        package=package.id,
        savings=package.savings,
        scenario=scenario,
        horizon=horizon,
        threshold=threshold,
        probability_below=per_checkpoint[worst],
        worst_checkpoint=worst,
        final=at[horizon],
        crossing=crossing_range(current, at, threshold),
    )


class Recommendation(BaseModel):
    """The least restrictive package that meets the target, or None, with every package's outcome."""

    target: float
    meets_target: bool
    chosen: str | None
    outcomes: list[Outcome]
    note: str


def optimise(
    current: float,
    forecast: Forecast,
    packages: Sequence[Package],
    *,
    scenario: Scenario,
    horizon: int,
    threshold: float,
    target: float = 0.10,
    ceiling: float | None = None,
) -> Recommendation:
    """Packages must be ordered from the least to the most restrictive (savings never decrease)."""
    if any(a.savings > b.savings for a, b in pairwise(packages)):
        raise ValueError("packages must be ordered from least to most restrictive")
    outcomes = [
        evaluate(current, forecast, package=p, scenario=scenario, horizon=horizon, threshold=threshold, ceiling=ceiling)
        for p in packages
    ]
    chosen = next((o for o in outcomes if o.probability_below <= target), None)
    if chosen:
        note = f"{chosen.package} is the least restrictive package with P(below {threshold:g}) <= {target:.0%}."
    else:
        best = outcomes[-1]
        note = (
            f"No package brings P(below {threshold:g}) within {target:.0%}; the most restrictive "
            f"({best.package}) leaves {best.probability_below:.0%}."
        )
    return Recommendation(
        target=target, meets_target=chosen is not None, chosen=chosen.package if chosen else None, outcomes=outcomes,
        note=note,
    )  # fmt: skip
