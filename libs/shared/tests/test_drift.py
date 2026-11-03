"""Tests for ai_circus_shared.drift — reference profiles and PSI."""

from __future__ import annotations

import json
import math
import random

import pytest

from ai_circus_shared import drift
from ai_circus_shared.drift import DriftWindow, bin_counts, build_reference, categorical_profile, numeric_profile, psi

RNG = random.Random(7)
TRAIN = [RNG.gauss(50, 10) for _ in range(5_000)]


def test_numeric_profile_has_deciles_and_a_missing_bin() -> None:
    profile = numeric_profile([*TRAIN, None, float("nan")])
    assert profile is not None
    assert len(profile["edges"]) == 9
    assert len(profile["expected"]) == 11  # 10 bins + missing
    assert sum(profile["expected"]) == pytest.approx(1.0, abs=1e-4)
    assert all(share == pytest.approx(0.1, abs=0.01) for share in profile["expected"][:-1])
    assert profile["expected"][-1] == pytest.approx(2 / 5_002, abs=1e-4)


def test_categorical_profile_keeps_top_categories_then_other_then_missing() -> None:
    values = ["a"] * 50 + ["b"] * 30 + [f"rare{i}" for i in range(20)] + [None] * 10
    profile = categorical_profile(values)
    assert profile is not None
    assert profile["categories"][:2] == ["a", "b"]
    assert len(profile["categories"]) == drift.MAX_CATEGORIES
    assert bin_counts(profile, ["a", "zzz", None]) == [1] + [0] * 9 + [1, 1]


def test_psi_is_near_zero_for_the_same_distribution_and_large_for_a_shift() -> None:
    profile = numeric_profile(TRAIN)
    assert profile is not None
    same = [RNG.gauss(50, 10) for _ in range(2_000)]
    shifted = [RNG.gauss(65, 10) for _ in range(2_000)]
    assert psi(profile["expected"], bin_counts(profile, same)) < drift.PSI_MODERATE
    assert psi(profile["expected"], bin_counts(profile, shifted)) > drift.PSI_SIGNIFICANT


def test_psi_needs_enough_rows() -> None:
    profile = numeric_profile(TRAIN)
    assert profile is not None
    assert psi(profile["expected"], bin_counts(profile, TRAIN[:10])) is None


def test_reference_is_small_json_without_raw_values() -> None:
    columns = {
        "age": TRAIN,
        "country": ["ES", "FR", "DE"] * 1_666 + ["ES", "ES"],
        "note": ["free text"] * 5_000,
        "unused": TRAIN,
    }
    kinds = {"age": "numeric", "country": "categorical", "note": "text", "unused": "numeric"}
    reference = build_reference(columns, kinds, ["note", "age", "country"], scores=[x / 100 for x in TRAIN])
    assert list(reference["features"]) == ["age", "country"]  # ranked order, text skipped, unranked ignored
    assert reference["score"]["kind"] == "numeric"
    assert len(json.dumps(reference)) < 3_000
    assert str(round(TRAIN[0], 6)) not in json.dumps(reference)


def test_build_reference_caps_the_number_of_features() -> None:
    columns = {f"f{i}": TRAIN for i in range(20)}
    reference = build_reference(columns, dict.fromkeys(columns, "numeric"), list(columns), scores=TRAIN)
    assert len(reference["features"]) == drift.MAX_FEATURES


def test_drift_window_reports_a_shifted_feature_and_starts_afresh_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    reference = build_reference({"x": TRAIN}, {"x": "numeric"}, ["x"], scores=TRAIN)
    window = DriftWindow(reference)
    assert window.feature_psi() == {} and window.score_psi() is None
    shifted = [RNG.gauss(70, 10) for _ in range(500)]
    window.observe({"x": shifted}, scores=shifted)
    assert window.feature_psi()["x"] > drift.PSI_SIGNIFICANT
    assert window.score_psi() > drift.PSI_SIGNIFICANT
    monkeypatch.setattr(drift, "WINDOW_ROWS", 500)
    calm = [RNG.gauss(50, 10) for _ in range(500)]
    window.observe({"x": calm}, scores=calm)  # the full window is dropped first
    assert window.rows == 500
    assert window.feature_psi()["x"] < drift.PSI_MODERATE


def test_values_that_are_not_numbers_count_as_missing() -> None:
    profile = numeric_profile(TRAIN)
    assert profile is not None
    assert bin_counts(profile, ["n/a", True, math.inf])[-1] == 3


@pytest.mark.parametrize(("count", "limit", "expected"), [(5, 10, 5), (10_000, 1_000, 1_000), (1_001, 1_000, 501)])
def test_sample_rows(count: int, limit: int, expected: int) -> None:
    assert len(drift.sample_rows(count, limit)) == expected
