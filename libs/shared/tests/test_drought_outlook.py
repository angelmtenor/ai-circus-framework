"""ai_circus_shared.drought_outlook — one system's outlook from the rulebook, the readings and a forecast."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from ai_circus_shared.drought_outlook import outlook
from ai_circus_shared.rulebook import Rulebook, load

RULEBOOK = Path(__file__).resolve().parents[3] / "scenarios" / "water_reservoirs_drought" / "rulebook" / "rulebook.yaml"
pytestmark = pytest.mark.skipif(not RULEBOOK.exists(), reason="scenario not present")

START = date(2025, 1, 1)
FORECAST = {
    7: {"p10": 44.0, "p50": 46.0, "p90": 48.0},
    30: {"p10": 36.0, "p50": 42.0, "p90": 47.0},
    90: {"p10": 22.0, "p50": 33.0, "p90": 45.0},
    180: {"p10": 10.0, "p50": 25.0, "p90": 44.0},
}


@pytest.fixture(scope="module")
def book() -> Rulebook:
    return load(RULEBOOK)


def _history(values: list[float | None]) -> list[tuple[date, float | None]]:
    return [(START + timedelta(days=i), v) for i, v in enumerate(values)]


def test_state_follows_the_machine_not_the_raw_reading(book: Rulebook) -> None:
    # 70 % (normal), a dip to 50 % (pre-alert), then back above 60 % for only 3 days: still pre-alert (7 needed)
    out = outlook(book, "s", _history([70.0] * 5 + [50.0] * 5 + [65.0] * 3), FORECAST)
    assert out["raw_state"] == "normal" and out["state"] == "pre_alert"
    assert out["days_in_state"] == 8
    assert [t["to"] for t in out["recent_transitions"]] == ["pre_alert"]


def test_forecast_states_and_the_next_threshold(book: Rulebook) -> None:
    out = outlook(book, "s", _history([48.0] * 10), FORECAST)
    assert out["state"] == "pre_alert"
    assert out["forecast_states"]["90"] == {"p10": "exceptional", "p50": "alert", "p90": "pre_alert"}
    nxt = out["crossings"][0]
    assert nxt["state_below"] == "alert" and nxt["threshold_pct"] == 40
    assert nxt["pessimistic"] < nxt["median"] < (nxt["optimistic"] or 1e9)
    assert [c["state_below"] for c in out["crossings"]] == ["alert", "exceptional", "emergency"]


def test_what_if_orders_the_scenarios_and_the_optimiser_answers(book: Rulebook) -> None:
    out = outlook(book, "s", _history([48.0] * 10), FORECAST)
    p = {s: out["what_if"]["no_measures"][s]["probability_below"] for s in ("dry", "normal", "wet")}
    assert p["dry"] >= p["normal"] >= p["wet"]
    opt = out["optimiser"]
    assert opt["scenario"] == "dry" and opt["horizon"] == 180 and opt["emergency_threshold_pct"] == 15
    assert [o["package"] for o in opt["outcomes"]] == ["normal", "pre_alert", "alert", "exceptional", "emergency"]
    assert opt["note"]


def test_a_gap_at_the_end_uses_the_last_published_reading(book: Rulebook) -> None:
    out = outlook(book, "s", _history([48.0, 47.0, None, None]), FORECAST)
    assert out["current_pct"] == 47.0 and out["as_of"] == (START + timedelta(days=1)).isoformat()


def test_no_reading_at_all_is_an_error(book: Rulebook) -> None:
    with pytest.raises(ValueError, match="no published reading"):
        outlook(book, "s", _history([None, None]), FORECAST)
