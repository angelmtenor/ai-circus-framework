"""ai_circus_shared.series_quality — every check, the score and the training policy."""

from __future__ import annotations

import random
from datetime import date, timedelta

from ai_circus_shared.open_data import Observation
from ai_circus_shared.series_quality import (
    DUPLICATE,
    FLATLINE,
    JUMP,
    LEVEL_VOLUME,
    PCT_RANGE,
    QualityConfig,
    check,
    cleaned,
    score_for,
)

START = date(2024, 1, 1)


def _series(pcts: list[float | None], name: str = "R", *, skip: set[int] = frozenset()) -> list[Observation]:  # type: ignore[assignment]
    return [
        Observation(series=name, day=START + timedelta(days=i), pct=p, volume=None if p is None else p / 10)
        for i, p in enumerate(pcts)
        if i not in skip
    ]


def _codes(report, code: str) -> list[date]:
    return [f.day for f in report.flags if f.code == code]


def test_a_clean_slowly_moving_series_has_no_flags_and_scores_100() -> None:
    report = check(_series([50 + 0.1 * i for i in range(60)]), QualityConfig(flatline_days=5))
    assert report.flags == [] and report.gaps == []
    assert report.scores == {"R": 100.0}


def test_duplicates_and_gaps() -> None:
    obs = _series([50.0 + i for i in range(10)], skip={3, 4, 5})
    obs.append(Observation(series="R", day=START + timedelta(days=7), pct=99.0, volume=9.9))
    report = check(obs, QualityConfig(max_daily_jump_pct=50))
    assert _codes(report, DUPLICATE) == [START + timedelta(days=7)]
    assert [(g.start, g.days) for g in report.gaps] == [(START + timedelta(days=3), 3)]
    assert report.counts()["gap"] == 3
    # 4 problem days (3 missing + 1 duplicate) out of 10 expected
    assert report.scores["R"] == 60.0


def test_flatline_needs_the_configured_run_length_and_contiguity() -> None:
    cfg = QualityConfig(flatline_days=4, max_daily_jump_pct=50)
    assert len(_codes(check(_series([40, 40, 40, 41, 42]), cfg), FLATLINE)) == 0  # 3 days only
    assert len(_codes(check(_series([40, 40, 40, 40, 41]), cfg), FLATLINE)) == 4
    # an interrupted run does not count as one
    assert len(_codes(check(_series([40, 40, 40, 40, 40], skip={2}), cfg), FLATLINE)) == 0


def test_jump_and_pct_range_are_flagged_on_the_day_they_appear() -> None:
    report = check(_series([50, 51, 70, 71, 105, 72]), QualityConfig(max_daily_jump_pct=5, flatline_days=9))
    assert START + timedelta(days=2) in _codes(report, JUMP)
    assert _codes(report, PCT_RANGE) == [START + timedelta(days=4)]
    assert all("needs review" in f.detail or f.code for f in report.flags)


def test_a_jump_across_a_gap_is_not_a_daily_jump() -> None:
    report = check(_series([50, 51, 52, 80, 81], skip={3}), QualityConfig(max_daily_jump_pct=5))
    assert _codes(report, JUMP) == []


def test_level_volume_outlier_is_found() -> None:
    rng = random.Random(7)
    obs = []
    for i in range(400):
        level = 100 + i * 0.1
        volume = (level - 99) ** 1.5 + rng.uniform(-0.05, 0.05)
        if i == 250:
            volume += 40  # a reading that does not fit the series' own curve
        obs.append(
            Observation(series="R", day=START + timedelta(days=i), level=level, volume=volume, pct=50.0 + i * 0.01)
        )
    report = check(obs, QualityConfig(flatline_days=999))
    assert _codes(report, LEVEL_VOLUME) == [START + timedelta(days=250)]


def test_a_revised_curve_is_not_flagged_when_fitted_per_window() -> None:
    obs = []
    for i in range(1095):
        level = 100 + (i % 365) * 0.05
        factor = 1.0 if i < 730 else 1.3  # the level -> volume table is revised after two years
        obs.append(Observation(series="R", day=START + timedelta(days=i), level=level, volume=(level - 99) * factor))
    whole = check(obs, QualityConfig(flatline_days=999, curve_min_abs_residual=0.01))
    per_year = check(obs, QualityConfig(flatline_days=999, curve_window_days=365, curve_min_abs_residual=0.01))
    assert len(_codes(whole, LEVEL_VOLUME)) > 100
    assert _codes(per_year, LEVEL_VOLUME) == []


def test_score_for_a_period_and_missing_values_do_not_crash() -> None:
    obs = _series([50, 51, None, 52, 150, 53, 54])
    report = check(obs, QualityConfig(max_daily_jump_pct=500, flatline_days=9))
    assert score_for(report, "R", START, START + timedelta(days=6)) == round(100 * 6 / 7, 2)
    assert score_for(report, "R", START + timedelta(days=5), START + timedelta(days=6)) == 100.0
    assert score_for(report, "R", START, START - timedelta(days=1)) == 100.0


def test_cleaned_applies_the_documented_policy_to_a_copy() -> None:
    obs = _series([50, 51, 52, 90, 53, 54, 55], skip={5})  # a spike on day 3, a one-day gap at 5
    obs.append(Observation(series="R", day=START + timedelta(days=1), pct=88.0, volume=8.8))  # duplicate
    cfg = QualityConfig(max_daily_jump_pct=10, flatline_days=9)
    report = check(obs, cfg)
    out, notes = cleaned(obs, report, cfg)
    # a spike is two jumps (up, then down): days 3 and 4 are excluded, then 3..5 is a 3-day gap, filled
    assert notes == {"duplicates_dropped": 1, "excluded": 2, "imputed_days": 3}
    by_day = {o.day: o.pct for o in out}
    assert by_day[START + timedelta(days=1)] == 51  # the first reading is kept
    assert by_day[START + timedelta(days=3)] == 52.75  # 52 .. 55 across the gap
    assert by_day[START + timedelta(days=5)] == 54.25
    assert len(obs) == 7  # the original is untouched


def test_a_long_gap_stays_a_gap() -> None:
    obs = _series([50, 51, 52, 53, 54, 55], skip={1, 2, 3, 4})
    out, notes = cleaned(obs, check(obs), QualityConfig(max_impute_gap_days=3))
    assert notes["imputed_days"] == 0 and len(out) == 2
