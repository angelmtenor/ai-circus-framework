"""
- Title:    Tests for the semantic scenario search: ranking, cache, entitlement filter and the lexical fallback
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

from types import SimpleNamespace

from platform_registry.core import scenario_search
from platform_registry.core.scenario_search import ScenarioSearch

VOCAB = ["water", "reservoir", "drought", "bank", "fraud", "money", "insurance", "customer"]


def _embed(text: str) -> list[float]:
    words = text.lower().split()
    return [float(sum(w.startswith(v) for w in words)) + 0.01 for v in VOCAB]


def test_search_ranks_by_meaning_caches_queries_and_keeps_to_the_allowed() -> None:
    calls: list[str] = []

    def embed_query(text: str) -> list[float]:
        calls.append(text)
        return _embed(text)

    index = ScenarioSearch()
    assert not index.ready and index.search("water", {"water"}) == []
    index.build(
        {
            "water": "Reservoirs and drought watch",
            "fraud": "Bank fraud and money mules",
            "lapse": "Insurance customer retention",
        },
        lambda texts: [_embed(t) for t in texts],
        embed_query,
    )
    assert index.ready
    ranked = index.search("a drought in the reservoir", {"water", "fraud", "lapse"})
    assert ranked[0][0] == "water"
    assert next(slug for slug, _ in index.search("bank money", {"water", "fraud"})) == "fraud"
    assert {slug for slug, _ in index.search("bank money", {"water"})} == {"water"}  # never outside the entitlements
    index.search("A drought in the  reservoir", {"water"})
    assert len(calls) == 2  # the same query (case and spaces aside) is embedded once


def test_the_scenario_text_reads_title_description_story_and_questions() -> None:
    row = SimpleNamespace(
        title="Reservoirs",
        description="Storage forecast.",
        industry="public_sector",
        story={"headline": "Where is the water heading?", "problem": "Weekly planning.", "audience": ["Planners"]},
        sample_questions=["When does it enter alert?"],
    )
    text = scenario_search.scenario_text(row)
    assert "Where is the water heading?" in text and "public sector" in text and "Planners" in text
