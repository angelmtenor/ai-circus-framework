"""The drought outlook of one supply system: observed state, forecast states, days to the next threshold, a rainfall
what-if, and the least restrictive measure package. Everything is computed from three inputs (the rulebook, the daily
observed indicator, a quantile forecast), so a tool, a bulletin and a test all get the same numbers.

Illustrative decision support: it never declares a state. The "observed" state is the rulebook's state machine run on
the published readings; every forecast and what-if number carries the uncertainty of the forecast it came from.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from ai_circus_shared.decision_support import Forecast, Scenario, evaluate, optimise
from ai_circus_shared.rulebook import Rulebook
from ai_circus_shared.threshold_states import crossing_range, run_states

SCENARIOS: tuple[Scenario, ...] = ("dry", "normal", "wet")
MAX_TRANSITIONS = 24
FULL = 100.0  # a scenario never ends above a full system


def _transitions(days: Sequence[date], states: Sequence[str | None]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    previous: str | None = None
    for day, state in zip(days, states, strict=True):
        if state is not None and previous is not None and state != previous:
            out.append({"day": day.isoformat(), "from": previous, "to": state})
        if state is not None:
            previous = state
    return out[-MAX_TRANSITIONS:]


def outlook(
    book: Rulebook,
    system_id: str,
    observed: Sequence[tuple[date, float | None]],
    forecast: Forecast,
    *,
    what_if_horizon: int = 90,
    optimise_horizon: int = 180,
    target: float = 0.10,
) -> dict[str, Any]:
    """`observed` is the indicator per day, oldest first (None where unpublished); `forecast` maps a horizon in days
    to its p10/p50/p90 for the same indicator."""
    model = book.state_model()
    ids = model.ids()
    days = [d for d, _ in observed]
    values = [v for _, v in observed]
    states = run_states(values, model, book.hysteresis_days)
    last = max((i for i, v in enumerate(values) if v is not None), default=None)
    if last is None:
        raise ValueError(f"{system_id}: no published reading")
    current_pct = float(values[last])  # type: ignore[arg-type]
    state = states[last]
    assert state is not None
    since = last
    while since > 0 and states[since - 1] == state:
        since -= 1

    index = ids.index(state)
    thresholds = [
        {"state_below": ids[i + 1], "threshold_pct": model.bands[i].min_value}
        for i in range(index, len(ids) - 1)
        if model.bands[i].min_value is not None
    ]
    crossings = [
        {**t, **crossing_range(current_pct, forecast, float(t["threshold_pct"])).model_dump()} for t in thresholds
    ]

    packages = book.packages()
    none = packages[0]
    emergency_threshold = float(model.bands[-2].min_value)  # type: ignore[arg-type]
    what_if = {
        s: evaluate(
            current_pct,
            forecast,
            package=none,
            scenario=s,
            horizon=what_if_horizon,
            threshold=emergency_threshold,
            ceiling=FULL,
        ).model_dump()
        for s in SCENARIOS
    }
    # For the next threshold too: when does each scenario enter the next worse state, with no measures?
    next_crossing = None
    if thresholds:
        next_threshold = float(thresholds[0]["threshold_pct"])
        next_crossing = {
            s: evaluate(
                current_pct, forecast, package=none, scenario=s, horizon=what_if_horizon, threshold=next_threshold
            ).crossing.model_dump()
            for s in SCENARIOS
        }
    recommendation = optimise(
        current_pct, forecast, packages, scenario="dry", horizon=optimise_horizon, threshold=emergency_threshold,
        target=target, ceiling=FULL,
    )  # fmt: skip

    def change(days_back: int) -> float | None:
        """Percentage-point change over the last `days_back` days, from the readings on those two exact dates."""
        wanted = days[last] - timedelta(days=days_back)
        before = next((v for d, v in reversed(observed[: last + 1]) if d == wanted and v is not None), None)
        return None if before is None else round(current_pct - before, 2)

    return {
        "system": system_id,
        "as_of": days[last].isoformat(),
        "current_pct": round(current_pct, 2),
        "change_7d_pp": change(7),
        "change_30d_pp": change(30),
        "state": state,
        "raw_state": model.classify(current_pct),
        "days_in_state": (days[last] - days[since]).days + 1,
        "hysteresis_days": book.hysteresis_days,
        "recent_transitions": _transitions(days, states),
        "forecast_states": {
            str(h): {name: model.classify(v) for name, v in q.items()} for h, q in sorted(forecast.items())
        },
        "crossings": crossings,
        "what_if": {
            "horizon": what_if_horizon,
            "emergency_threshold_pct": emergency_threshold,
            "no_measures": what_if,
            "next_threshold_crossing": next_crossing,
        },
        "optimiser": {
            "scenario": "dry",
            "horizon": optimise_horizon,
            "emergency_threshold_pct": emergency_threshold,
            **recommendation.model_dump(),
        },
        "note": "Illustrative decision support on synthetic data. A person decides.",
    }


def history_from_series(rows: Mapping[date, float | None]) -> list[tuple[date, float | None]]:
    """A day -> value mapping as the oldest-first list `outlook` takes."""
    return sorted(rows.items())
