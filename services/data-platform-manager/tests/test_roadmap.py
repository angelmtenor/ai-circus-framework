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


def test_every_capability_has_a_valid_pillar() -> None:
    for capability in get_roadmap():
        assert capability.pillar in {"data", "ai-bi-ml", "governance"}


def test_every_pillar_is_represented() -> None:
    pillars = {c.pillar for c in get_roadmap()}
    assert pillars == {"data", "ai-bi-ml", "governance"}


def test_non_relational_and_cache_are_reported_live_via_this_services_own_adoption() -> None:
    by_name = {c.name: c for c in get_roadmap()}
    assert by_name["Non-Relational Store"].status == "live"
    assert by_name["Cache / Key-Value"].status == "live"


def test_governance_capabilities_are_listed_with_honest_statuses() -> None:
    by_name = {c.name: c for c in get_roadmap() if c.pillar == "governance"}
    shipped = [
        "AI-system metadata",
        "AI-BOM inventory export",
        "GenAI trace attributes",
        "ML decision records",
        "Policy decision point",
        "Governance events",
        "Admin audit trail",
    ]
    for name in shipped:
        assert by_name[name].status == "live", name
    for name in (
        "Named admin identities in the audit trail",
        "Per-AI-system tool scoping and agent step limits",
        "SPDX 3.0 AI inventory profile",
    ):
        assert by_name[name].status == "planned", name


def test_governance_notes_never_claim_compliance() -> None:
    for capability in get_roadmap():
        if capability.pillar == "governance":
            assert "compliant" not in capability.note.lower(), capability.name
