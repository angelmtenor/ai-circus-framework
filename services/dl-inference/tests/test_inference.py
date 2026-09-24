"""Tests for core/inference.py — numerics, input validation and explanations, on real
(tiny) ONNX models whose correct explanation is known by construction.
"""

from __future__ import annotations

import base64

import numpy as np
import pytest

from dl_inference.core import inference
from dl_inference.core.model_cache import DlModelCache, LoadedModel
from tests.fixtures import IMAGE_SIZE, MemoryStore, bright_quadrant_image, png, publish


def _model(modality: str) -> LoadedModel:
    store = MemoryStore()
    publish(store, "demo", modality)
    return DlModelCache({"x": store}, fallback_org_id="demo", threads=1).get("demo", "x")  # type: ignore[dict-item]


def test_log_softmax_and_log_odds_stay_exact_when_the_model_is_saturated() -> None:
    logits = np.array([[40.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    assert np.allclose(np.exp(inference.log_softmax(logits)).sum(axis=1), 1.0)
    # float32 softmax would round p0 to exactly 1.0 here — log-odds must still be 40.
    assert inference.log_odds(logits, 0)[0] == pytest.approx(40.0)
    assert inference.log_odds(logits, 1)[1] == pytest.approx(1.0)
    three = np.array([[2.0, 1.0, 0.0]])
    assert inference.log_odds(three, 0)[0] == pytest.approx(2.0 - np.log(np.e + 1.0))


def test_banzhaf_weights_recover_an_additive_model_exactly() -> None:
    rng = np.random.default_rng(0)
    keep = rng.random((80, 5)) < 0.5
    true = np.array([2.0, -1.0, 0.0, 0.5, 3.0])
    outcomes = keep @ true + 7.0
    assert np.allclose(inference.banzhaf_weights(keep, outcomes), true, atol=1e-2)


def test_text_prediction_and_word_attributions() -> None:
    model = _model("text")
    text = "i have fever and rash"
    scored = inference.score_text(model, text)
    assert int(scored.probs.argmax()) == 0  # dengue
    explanation = inference.explain_text(model, text, target=0)
    weights = {t["text"]: t["weight"] for t in explanation["tokens"]}
    assert set(weights) == {"i", "have", "fever", "and", "rash"}
    assert weights["fever"] > weights["rash"] > 0.1
    assert abs(weights["have"]) < weights["rash"]
    # Offsets point back into the original text.
    fever = next(t for t in explanation["tokens"] if t["text"] == "fever")
    assert text[fever["start"] : fever["end"]] == "fever"
    # Deterministic: the same text always gets the same explanation.
    assert inference.explain_text(model, text, target=0) == explanation


def test_text_explanation_without_a_mask_token_drops_words_instead() -> None:
    model = _model("text")
    model.metadata["preprocessing"]["mask_token_id"] = None
    weights = {t["text"]: t["weight"] for t in inference.explain_text(model, "sneezing and nose", 1)["tokens"]}
    assert weights["sneezing"] > 0


def test_clean_text_validation() -> None:
    assert inference.clean_text("  fever ") == "fever"
    with pytest.raises(inference.InvalidInputError, match="empty"):
        inference.clean_text("   ")
    with pytest.raises(inference.InvalidInputError, match="longer"):
        inference.clean_text("x" * (inference.MAX_TEXT_CHARS + 1))


def test_image_prediction_and_occlusion_heatmap_finds_the_hotspot() -> None:
    model = _model("image")
    image = bright_quadrant_image()
    scored = inference.score_image(model, image)
    assert int(scored.probs.argmax()) == 1
    explanation = inference.explain_image(model, image, 1, scored.logits)
    grid = np.array(explanation["grid"])
    assert grid.shape == (4, 4)
    # Only the top-left quadrant (cells [0:2, 0:2]) carries evidence for class 1.
    assert grid[:2, :2].min() > 0.1
    assert np.abs(grid[2:, :]).max() < 1e-6 and np.abs(grid[:, 2:]).max() < 1e-6


def test_decode_image_resizes_converts_and_rejects_garbage() -> None:
    preprocessing = {"size": IMAGE_SIZE, "source_mode": "L"}
    rgb = np.zeros((40, 30, 3), dtype=np.uint8)
    decoded = inference.decode_image(png(rgb), preprocessing)
    assert decoded.shape == (IMAGE_SIZE, IMAGE_SIZE)
    data_url = "data:image/png;base64," + base64.b64encode(png(rgb)).decode()
    assert inference.decode_base64_image(data_url, preprocessing).shape == (IMAGE_SIZE, IMAGE_SIZE)
    with pytest.raises(inference.InvalidInputError, match="readable"):
        inference.decode_image(b"definitely not an image", preprocessing)
    with pytest.raises(inference.InvalidInputError, match="base64"):
        inference.decode_base64_image("***", preprocessing)
    with pytest.raises(inference.InvalidInputError, match="larger"):
        inference.decode_image(b"0" * (inference.MAX_IMAGE_BYTES + 1), preprocessing)


def test_similar_cases_rank_by_cosine_similarity() -> None:
    model = _model("text")
    similar = inference.similar_cases(model, np.array([0.1, 0.9, 0.0, 0.0]), k=2)
    assert [c["id"] for c in similar] == ["r-1", "r-0"]
    assert similar[0]["text"] == "nose"
    assert inference.similar_cases(model, np.ones(4), k=0) == []


def test_png_base64_roundtrip() -> None:
    encoded = inference.png_base64(bright_quadrant_image())
    assert base64.b64decode(encoded).startswith(b"\x89PNG")


def test_calibration_temperature_softens_probabilities_but_never_changes_the_prediction() -> None:
    model = _model("text")
    sharp = inference.score_text(model, "fever and rash")
    model.metadata["temperature"] = 4.0
    soft = inference.score_text(model, "fever and rash")
    assert int(soft.probs.argmax()) == int(sharp.probs.argmax())
    assert soft.probs.max() < sharp.probs.max()
    assert np.allclose(soft.logits * 4.0, sharp.logits, atol=1e-5)
