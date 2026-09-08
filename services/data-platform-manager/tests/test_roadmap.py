"""Tests for core.roadmap — structural sanity, not a live probe (see that
module's docstring for why status here is hand-maintained, not inferred).
"""

from __future__ import annotations

from data_platform_manager.core.roadmap import get_roadmap


def test_roadmap_is_non_empty() -> None:
    assert len(get_roadmap()) > 0


def test_every_capability_has_a_valid_status() -> None:
    for capability in get_roadmap():
        assert capability.status in {"live", "partial", "planned"}


def test_every_capability_has_a_non_empty_note() -> None:
    for capability in get_roadmap():
        assert capability.note.strip() != ""


def test_non_relational_and_cache_are_reported_live_via_this_services_own_adoption() -> None:
    by_name = {c.name: c for c in get_roadmap()}
    assert by_name["Non-Relational Store"].status == "live"
    assert by_name["Cache / Key-Value"].status == "live"
