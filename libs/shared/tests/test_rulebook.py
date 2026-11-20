"""ai_circus_shared.rulebook — one YAML, and the plan, the engine and the packages generated from it."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_circus_shared.rulebook import Rulebook, expected_files, load, markdown, paragraphs

RULEBOOK_DIR = Path(__file__).resolve().parents[3] / "scenarios" / "water_reservoirs_drought" / "rulebook"

pytestmark = pytest.mark.skipif(not (RULEBOOK_DIR / "rulebook.yaml").exists(), reason="scenario not present")


@pytest.fixture(scope="module")
def book() -> Rulebook:
    return load(RULEBOOK_DIR / "rulebook.yaml")


def test_committed_markdown_is_generated_from_the_yaml(book: Rulebook) -> None:
    for path, text in expected_files(book, RULEBOOK_DIR).items():
        assert path.exists(), f"{path.name} missing: run scripts/generate_rulebook.py"
        assert path.read_text(encoding="utf-8") == text, f"{path.name} differs from rulebook.yaml"


def test_state_model_matches_the_plan_table(book: Rulebook) -> None:
    model = book.state_model()
    assert model.ids() == ["normal", "pre_alert", "alert", "exceptional", "emergency"]
    assert [model.classify(v) for v in (80, 60, 59.9, 40, 25, 24.9, 15, 14.9)] == [
        "normal", "normal", "pre_alert", "pre_alert", "alert", "exceptional", "exceptional", "emergency",
    ]  # fmt: skip
    assert book.hysteresis_days == 7


def test_packages_are_ordered_and_follow_the_measures(book: Rulebook) -> None:
    packages = book.packages()
    savings = [p.savings for p in packages]
    assert savings == sorted(savings) and savings[0] == 0
    alert = next(p for p in packages if p.id == "alert")
    assert alert.savings == pytest.approx(0.4 * 0.20 + 0.1 * 0.05 + 0.5 * (1 - 250 / 280), abs=1e-4)


def test_every_paragraph_is_numbered_and_citable(book: Rulebook) -> None:
    for lang in book.languages:
        items = paragraphs(book, lang)
        numbers = [p.number for p in items]
        assert len(numbers) == len(set(numbers))
        assert all(p.citation == f"Art. {p.number}" for p in items)
    en = {p.number: p.text for p in paragraphs(book, "en")}
    assert "Agriculture" in en["8.1"] and "40" in en["8.1"]  # exceptional: agriculture -40 %
    assert "230" in en["8.3"]


def test_both_languages_have_the_same_structure(book: Rulebook) -> None:
    assert [p.number for p in paragraphs(book, "en")] == [p.number for p in paragraphs(book, "es")]
    assert "Agricultura" in {p.number: p.text for p in paragraphs(book, "es")}["8.1"]


def test_the_hysteresis_in_the_text_is_the_one_in_the_config(book: Rulebook) -> None:
    text = markdown(book, "en")
    assert f"{book.hysteresis_days} consecutive days" in text
    assert text.splitlines()[2].startswith("> Fictional plan")


def test_a_missing_translation_is_refused() -> None:
    raw = yaml.safe_load((RULEBOOK_DIR / "rulebook.yaml").read_text(encoding="utf-8"))
    del raw["states"][1]["label"]["es"]
    with pytest.raises(ValueError, match="label must be given"):
        Rulebook.model_validate(raw)


def test_demand_shares_must_sum_to_one() -> None:
    raw = yaml.safe_load((RULEBOOK_DIR / "rulebook.yaml").read_text(encoding="utf-8"))
    raw["demand"]["shares"]["urban"] = 0.9
    with pytest.raises(ValueError, match="sum to 1"):
        Rulebook.model_validate(raw)
