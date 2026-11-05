"""Unit tests for training.core.recommender."""

from __future__ import annotations

import pandas as pd
import pytest
from ai_circus_shared.scenario_schema import RecommenderConfig

from training.core.recommender import _split, association_rules, item_factors, train_recommender

CONFIG = RecommenderConfig(basket_column="basket", item_column="sku", label_column="name", top_k=2, min_baskets=2)


def _frame(baskets: list[list[str]]) -> pd.DataFrame:
    rows = [
        {"basket": f"b{i}", "sku": sku, "name": f"Name {sku}"} for i, basket in enumerate(baskets) for sku in basket
    ]
    return pd.DataFrame(rows)


def test_association_rules_rank_by_confidence_and_drop_coincidences() -> None:
    baskets = [["board", "fin"]] * 4 + [["board", "wax"]] * 2 + [["wax", "leash"]] * 3 + [["fin", "cap"]]
    rules = association_rules(baskets, top_k=5, min_baskets=2)
    assert [item for item, _ in rules["board"]] == ["fin", "wax"]
    assert rules["board"][0][1] == pytest.approx(4 / 6, abs=1e-3)
    assert "cap" not in [item for item, _ in rules["fin"]]  # seen together once: under min_baskets


def test_the_hidden_item_is_not_always_the_alphabetically_last() -> None:
    """Always hiding the last one would mostly hide wax and flatter the popularity baseline."""
    baskets = {f"b{i}": ["board", "wax"] for i in range(200)}
    _, test = _split(baskets)
    assert {hidden for _, hidden in test} == {"board", "wax"}


def test_item_factors_find_items_of_the_same_group() -> None:
    baskets = [["board", "fin"]] * 10 + [["kite", "bar"]] * 10
    neighbours = item_factors(baskets, top_k=1, factors=2)
    assert neighbours["board"][0][0] == "fin" and neighbours["kite"][0][0] == "bar"


def test_train_recommender_scores_every_candidate_and_keeps_the_simplest_that_holds_up() -> None:
    baskets = [["board", "fin"], ["kite", "bar"], ["sup", "paddle"]] * 80
    run = train_recommender(_frame(baskets), CONFIG, ["association_rules", "item_factors"], threshold=0.02)
    assert [c.name for c in run.candidates] == ["association_rules", "item_factors"]
    assert run.selected.name == "association_rules"  # both perfect: the simpler one stays
    assert run.selected.metrics["hit_rate"] == pytest.approx(1.0)
    assert run.document["items"]["fin"]["label"] == "Name fin"
    assert run.document["candidates"]["item_factors"]["board"][0]["item"] == "fin"


def test_train_recommender_refuses_too_few_baskets_to_evaluate() -> None:
    with pytest.raises(ValueError, match="too few to evaluate"):
        train_recommender(_frame([["a", "b"]]), CONFIG, ["association_rules"], threshold=0.02)
