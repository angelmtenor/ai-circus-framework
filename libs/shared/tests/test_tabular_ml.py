"""Tests for ai_circus_shared.tabular_ml's transformed-name helpers."""

from __future__ import annotations

import pytest

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


def test_checksum_signature_round_trips_and_detects_tampering(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_circus_shared.tabular_ml import checksums_signature_valid, model_signing_enabled, sign_checksums

    monkeypatch.delenv("MODEL_SIGNING_KEY", raising=False)
    assert not model_signing_enabled() and sign_checksums({"pipeline": "a"}) is None
    assert not checksums_signature_valid({"pipeline": "a"}, "anything")

    monkeypatch.setenv("MODEL_SIGNING_KEY", "s3cret")
    checksums = {"pipeline": "a", "explainer": "b"}
    signature = sign_checksums(checksums)
    assert model_signing_enabled() and signature is not None
    assert checksums_signature_valid({"explainer": "b", "pipeline": "a"}, signature)  # key order is irrelevant
    assert not checksums_signature_valid({**checksums, "pipeline": "tampered"}, signature)
    assert not checksums_signature_valid(checksums, None) and not checksums_signature_valid(checksums, 5)
    monkeypatch.setenv("MODEL_SIGNING_KEY", "other")
    assert not checksums_signature_valid(checksums, signature)
