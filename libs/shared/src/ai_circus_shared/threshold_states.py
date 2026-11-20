"""A state machine over a threshold-banded indicator, with hysteresis, and when a forecast crosses a threshold.

Generic: any indicator that is "worse when lower" and banded into ordered states (a drought plan's storage %,
a stock level, a battery charge). Pure Python.

Two rules make the observed state stable enough to act on:

- getting **worse** is immediate: the day the indicator falls into a worse band, the state follows;
- getting **better** takes `hysteresis_days` consecutive days with the indicator in a better band, and the state
  then moves to the *worst* of those days' bands. Each further improvement needs a fresh run of days.

A day with no reading changes nothing and does not count towards a run.

For forecasts, `crossing_days` reads a piecewise-linear path through (horizon, value) points; `crossing_range`
does it for the p10/p50/p90 paths. Those are *marginal* quantiles per horizon joined by straight lines, not
sampled paths, so the range is an indication, not a calibrated interval of the crossing date.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Band(BaseModel):
    """One state. `min_value` is its inclusive lower bound; the worst state has none."""

    model_config = ConfigDict(frozen=True)

    id: str
    min_value: float | None = None


class StateModel(BaseModel):
    """Bands ordered from best to worst, each with a strictly lower bound than the one before."""

    model_config = ConfigDict(frozen=True)

    bands: list[Band] = Field(min_length=2)

    @model_validator(mode="after")
    def _ordered(self) -> StateModel:
        bounds = [b.min_value for b in self.bands]
        if bounds[-1] is not None or any(b is None for b in bounds[:-1]):
            raise ValueError("every band but the last needs a min_value, and the last must have none")
        numbers = [b for b in bounds[:-1] if b is not None]
        if any(a <= b for a, b in pairwise(numbers)):
            raise ValueError("band bounds must be strictly decreasing from best to worst")
        if len({b.id for b in self.bands}) != len(self.bands):
            raise ValueError("band ids must be unique")
        return self

    def ids(self) -> list[str]:
        return [b.id for b in self.bands]

    def level(self, value: float) -> int:
        """Index of the band `value` falls in (0 = best)."""
        for i, band in enumerate(self.bands):
            if band.min_value is None or value >= band.min_value:
                return i
        return len(self.bands) - 1  # unreachable: the last band has no bound

    def classify(self, value: float) -> str:
        return self.bands[self.level(value)].id

    def lower_bound(self, state_id: str) -> float | None:
        """The value under which the indicator leaves `state_id` for the next worse one (None for the worst)."""
        return self.bands[self.ids().index(state_id)].min_value


def run_states(values: Sequence[float | None], model: StateModel, hysteresis_days: int) -> list[str | None]:
    """The state on each day. None until the first reading; a day without one repeats the previous state."""
    out: list[str | None] = []
    current: int | None = None
    better: list[int] = []
    for value in values:
        if value is None:
            out.append(None if current is None else model.bands[current].id)
            continue
        raw = model.level(value)
        if current is None:
            current = raw
        elif raw > current:
            current, better = raw, []
        elif raw < current:
            better.append(raw)
            if len(better) >= max(1, hysteresis_days):
                current, better = max(better[-max(1, hysteresis_days) :]), []
        else:
            better = []
        out.append(model.bands[current].id)
    return out


def crossing_days(points: Sequence[tuple[float, float]], threshold: float) -> float | None:
    """Days until a piecewise-linear path through (day, value) points first goes below `threshold`: 0 if it
    starts below, None if it stays at or above through the last point."""
    ordered = sorted(points)
    if not ordered:
        return None
    if ordered[0][1] < threshold:
        return float(ordered[0][0])
    for (d0, v0), (d1, v1) in pairwise(ordered):
        if v1 < threshold:
            return d0 + (d1 - d0) * (v0 - threshold) / (v0 - v1)
    return None


class CrossingRange(BaseModel):
    """When the p90 (optimistic), p50 and p10 (pessimistic) paths first go below a threshold; None = not within
    the forecast horizon."""

    threshold: float
    optimistic: float | None
    median: float | None
    pessimistic: float | None


def crossing_range(
    current: float, by_horizon: Mapping[int, Mapping[str, float]], threshold: float, *, low: str = "p10",
    mid: str = "p50", high: str = "p90",
) -> CrossingRange:  # fmt: skip
    """`by_horizon` maps a horizon in days to its quantiles, e.g. {30: {"p10": 61.0, "p50": 70.0, "p90": 78.0}}."""

    def path(name: str) -> list[tuple[float, float]]:
        return [(0.0, current)] + [(float(h), q[name]) for h, q in sorted(by_horizon.items())]

    def days(name: str) -> float | None:
        d = crossing_days(path(name), threshold)
        return None if d is None else round(d, 1)

    return CrossingRange(threshold=threshold, optimistic=days(high), median=days(mid), pessimistic=days(low))


def probability_below(quantiles: Mapping[float, float], threshold: float) -> float:
    """P(value < threshold) from a few quantiles (e.g. {0.1: 40, 0.5: 55, 0.9: 66}): linear between them, the
    outer segments carried on with their own slope, then clamped to [0.01, 0.99] because three quantiles
    cannot support a more confident statement."""
    knots = sorted((v, p) for p, v in quantiles.items())
    if len(knots) < 2:
        raise ValueError("need at least two quantiles")
    eps = 1e-9
    (v0, p0), (v1, p1) = knots[0], knots[1]
    (vn1, pn1), (vn, pn) = knots[-2], knots[-1]
    if threshold <= v0:
        p = p0 + (threshold - v0) * (p1 - p0) / max(v1 - v0, eps)
    elif threshold >= vn:
        p = pn + (threshold - vn) * (pn - pn1) / max(vn - vn1, eps)
    else:
        p = pn
        for (va, pa), (vb, pb) in pairwise(knots):
            if va <= threshold <= vb:
                p = pa + (threshold - va) * (pb - pa) / max(vb - va, eps)
                break
    return round(min(0.99, max(0.01, p)), 4)
