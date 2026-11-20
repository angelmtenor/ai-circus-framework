"""ai_circus_shared.drought_bulletin — the weekly bulletin, built only from computed outlooks."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from ai_circus_shared.drought_bulletin import build
from ai_circus_shared.drought_outlook import outlook
from ai_circus_shared.rulebook import Rulebook, load, paragraphs
from ai_circus_shared.traceability import untraceable

RULEBOOK = Path(__file__).resolve().parents[3] / "scenarios" / "water_reservoirs_drought" / "rulebook" / "rulebook.yaml"
pytestmark = pytest.mark.skipif(not RULEBOOK.exists(), reason="scenario not present")

START = date(2026, 1, 1)
FORECAST = {
    7: {"p10": 44.0, "p50": 46.0, "p90": 48.0},
    30: {"p10": 36.0, "p50": 42.0, "p90": 47.0},
    90: {"p10": 22.0, "p50": 33.0, "p90": 45.0},
    180: {"p10": 10.0, "p50": 25.0, "p90": 44.0},
}
CALM = {h: {"p10": 78.0, "p50": 82.0, "p90": 86.0} for h in FORECAST}


@pytest.fixture(scope="module")
def book() -> Rulebook:
    return load(RULEBOOK)


def _outlooks(book: Rulebook):
    low = [(START + timedelta(days=i), 48.0 + 0.1 * i) for i in range(40)]
    high = [(START + timedelta(days=i), 80.0 + 0.05 * i) for i in range(40)]
    return [outlook(book, "low", low, FORECAST), outlook(book, "high", high, CALM)]


def _bulletin(book: Rulebook, lang: str = "en", dq: dict | None = None) -> tuple[str, list]:
    outs = _outlooks(book)
    text = build(
        book,
        outs,
        {"low": FORECAST, "high": CALM},
        lang=lang,
        week_of=date(2026, 2, 9),
        system_names={"low": "Low system", "high": "High system"},
        dq_notes=dq or {},
        attribution=["Source A.", "Source B."],
    )
    return text, outs


def test_it_has_every_section_and_the_banner(book: Rulebook) -> None:
    text, _ = _bulletin(book)
    for heading in (
        "Situation by supply system",
        "Next threshold",
        "What-if headline",
        "Data-quality notes",
        "Applicable measures",
    ):
        assert f"## {heading}" in text
    assert "Reference implementation on synthetic data" in text and "It declares nothing" in text
    assert "Source A. Source B." in text


def test_every_figure_comes_from_the_outlooks_the_rulebook_or_the_inputs(book: Rulebook) -> None:
    text, outs = _bulletin(book, dq={"low": ["2 readings need review (flatline)"]})
    paras = paragraphs(book, "en")
    sources = [outs, FORECAST, CALM, [p.text for p in paras], [p.citation for p in paras], "2026-02-09", "2 readings"]
    assert untraceable(text, sources) == []


def test_measures_are_cited_only_for_systems_above_normal(book: Rulebook) -> None:
    text, _ = _bulletin(book)
    measures = text.split("## Applicable measures")[1]
    assert "Low system (Pre-alert)" in measures and "High system" not in measures
    assert "(Art. 6.1)" in measures  # pre-alert measures live in article 6


def test_all_normal_means_no_restrictions(book: Rulebook) -> None:
    outs = [outlook(book, "high", [(START + timedelta(days=i), 80.0) for i in range(10)], CALM)]
    text = build(book, outs, {"high": CALM}, lang="en", week_of=START, system_names={}, dq_notes={}, attribution=[])
    assert "no restrictions apply" in text


def test_spanish_and_an_unsupported_language(book: Rulebook) -> None:
    text, _ = _bulletin(book, "es")
    assert "# Boletín semanal de sequía" in text and "## Medidas aplicables" in text
    with pytest.raises(ValueError, match="no bulletin"):
        _bulletin(book, "fr")


def test_data_quality_notes_are_listed_by_system(book: Rulebook) -> None:
    text, _ = _bulletin(book, dq={"low": ["Highmoor: 3 readings need review"]})
    assert "- Low system: Highmoor: 3 readings need review" in text
    assert "No readings need review this week." not in text
