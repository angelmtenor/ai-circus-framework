"""Tests for ai_circus_shared.tabular_ml's transformed-name helpers."""

from __future__ import annotations

from ai_circus_shared.tabular_ml import KEPT_NEGATIONS, original_feature, text_term, text_transformer_name

FEATURES = ["Geography", "Age", "Review"]


def test_text_terms_roll_up_to_their_column_by_transformer_prefix() -> None:
    name = f"{text_transformer_name('Review')}__upper management"
    assert text_term(name, ["Review"]) == ("Review", "upper management")
    assert original_feature(name, FEATURES, ["Review"]) == "Review"
    # A term that happens to start with another feature's name still maps by prefix.
    assert original_feature("text_Review__agency", FEATURES, ["Review"]) == "Review"


def test_one_hot_and_numeric_columns_keep_the_startswith_rule() -> None:
    assert original_feature("cat__Geography_France", FEATURES, ["Review"]) == "Geography"
    assert original_feature("num__Age", FEATURES) == "Age"
    assert text_term("cat__Geography_France", ["Review"]) is None
    assert original_feature("num__Unknown", FEATURES) == "Unknown"


def test_negations_are_kept_out_of_the_stop_words() -> None:
    assert {"no", "not", "never"} <= KEPT_NEGATIONS


def test_the_longest_feature_name_wins_and_embeddings_roll_up() -> None:
    features = ["Review", "ReviewYear"]
    assert original_feature("num__ReviewYear", features, ["Review"]) == "ReviewYear"
    assert original_feature("num__Review__emb_7", features, ["Review"]) == "Review"
