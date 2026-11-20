"""ai_circus_shared.traceability — a check that an answer's figures came from its sources."""

from __future__ import annotations

from ai_circus_shared.traceability import numbers_in, untraceable


def test_numbers_are_normalised() -> None:
    assert numbers_in("12,50 and 12.5 and 7") == {"12.5", "7"}
    assert numbers_in("p50 = 47 days") == {"47"}  # 'p50' is a label, not a figure


def test_a_number_from_the_sources_passes_in_its_usual_rewrites() -> None:
    sources = [{"current_pct": 54.3, "probability": 0.1682, "days": 47, "day": "2026-11-20"}]
    assert untraceable("Storage is 54.3 % and 54 % rounded, 17 % likely, in 47 days on 2026-11-20", sources) == []
    assert untraceable("54,3 %", sources) == []


def test_an_invented_number_is_reported_once_and_in_order() -> None:
    sources = [{"pct": 40.0}]
    assert untraceable("It is 40 %, then 63 and 71 and 63 again", sources) == ["63", "71"]


def test_structure_numbers_are_ignored_but_can_be_unignored() -> None:
    assert untraceable("1. First, 2. Second", []) == []
    assert untraceable("1. First", [], ignore=frozenset()) == ["1"]


def test_nested_sources_and_booleans() -> None:
    sources = [{"rows": [["highmoor", 73.6], ["stonebridge", 61.2]], "ok": True}, "Art. 8.1 says 40"]
    assert untraceable("Highmoor 73.6, Stonebridge 61.2, Art. 8.1, 40", sources) == []
