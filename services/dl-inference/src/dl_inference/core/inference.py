"""
- Title:    Deep-learning inference + explainability on onnxruntime (no torch)
- Author:   ai-circus-framework contributors

Preprocessing here must match dl-training's `tasks.py` exactly (same tokenizer.json,
same resize/normalize), because the metrics in each model's manifest were measured
through that code path on the exported ONNX graph.

Explanations are perturbation-based (occlusion) — model-agnostic, faithful by
construction (they measure what the deployed model actually does when evidence is
removed) and need only forward passes, which is what keeps the serving image free of
torch/autograd:

- text: Banzhaf-value word attributions. Random coalitions of words (each kept with
  probability 1/2) are scored with the rest replaced by the tokenizer's [MASK] token,
  and the target's log-odds are regressed on the keep/mask indicators — each
  coefficient is the word's average marginal contribution over all contexts. Unlike
  one-word-at-a-time occlusion this credits *redundant* evidence (fever + rash + joint
  pain all pointing to dengue), which single removals miss. Positive = the word
  supports the class, negative = it argues against it.
- image: an N x N grid of patches, each blanked to the image's mean intensity in turn;
  the heatmap cell is the drop in the target's log-odds.
- image, task=anomaly_detection: no perturbation at all — the graph's own `anomaly_map`
  output (each patch's distance to the nearest normal patch) *is* the explanation,
  rescaled so that what unseen normal parts reach shows no heat (see explain_anomaly).

Every logit is first divided by the manifest's `temperature` (dl-training's post-hoc
calibration), so probabilities here are the calibrated ones the model card reports.

Log-odds (logit_t - logsumexp of the other logits) rather than probability: a
fine-tuned model is often 99.99% sure, and then removing one piece of evidence moves its
probability (or even log-probability) by ~1e-8 — every explanation would read as zero.
The log-odds move by O(1) whatever the confidence.

Plus example-based evidence: the most similar training cases by cosine similarity of
the model's own embedding (`embedding` ONNX output).
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from ai_circus_shared.deep_learning import (
    ONNX_ANOMALY_MAP_OUTPUT,
    ONNX_EMBEDDING_OUTPUT,
    ONNX_IMAGE_INPUT,
    ONNX_LOGITS_OUTPUT,
)
from PIL import Image, UnidentifiedImageError

from dl_inference.core.model_cache import LoadedModel

MAX_TEXT_CHARS = 4000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard (PIL's own default warns at ~89M)
# Perturbation batches are kept small on purpose: peak activation memory scales with
# batch size, and this pod's whole point is a small, bounded footprint.
OCCLUSION_BATCH = 8
TEXT_BATCH = 16
# Words (keeping in-word apostrophes/hyphens, incl. the typographic \u2019) and single punctuation marks.
_WORD = re.compile(r"\w+(?:['\u2019-]\w+)*|[^\w\s]", re.UNICODE)


class InvalidInputError(ValueError):
    """The request's text/image can't be scored (api.py maps it to a 422)."""


def log_softmax(logits: np.ndarray) -> np.ndarray:
    """Row-wise log-probabilities, computed in float64 from the logits."""
    logits = logits.astype(np.float64)
    shifted = logits - logits.max(axis=1, keepdims=True)
    return shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True))


def log_odds(logits: np.ndarray, target: int) -> np.ndarray:
    """Per row: log(p_target / (1 - p_target)) = logit_target - logsumexp(other logits)."""
    logits = logits.astype(np.float64)
    others = np.delete(logits, target, axis=1)
    peak = others.max(axis=1, keepdims=True)
    return logits[:, target] - (peak[:, 0] + np.log(np.exp(others - peak).sum(axis=1)))


@dataclass(frozen=True)
class Scored:
    """One input's class probabilities and embedding (+ anomaly map, anomaly detectors only)."""

    logits: np.ndarray  # (n_classes,)
    embedding: np.ndarray  # (dim,)
    anomaly_map: np.ndarray | None = None  # (grid, grid)

    @property
    def probs(self) -> np.ndarray:
        """Class probabilities."""
        return np.exp(log_softmax(self.logits[None])[0])


# ── text ──────────────────────────────────────────────────────────────────────


def _pad(rows: list[list[int]], pad_id: int) -> tuple[np.ndarray, np.ndarray]:
    width = max(len(r) for r in rows)
    ids = np.full((len(rows), width), pad_id, dtype=np.int64)
    mask = np.zeros((len(rows), width), dtype=np.int64)
    for i, row in enumerate(rows):
        ids[i, : len(row)] = row
        mask[i, : len(row)] = 1
    return ids, mask


def _temperature(model: LoadedModel) -> float:
    """Calibration temperature from the manifest (1.0 = uncalibrated / older models)."""
    return float(model.metadata.get("temperature") or 1.0)


def _run_text(model: LoadedModel, rows: list[list[int]]) -> tuple[np.ndarray, np.ndarray]:
    pad_id = int(model.metadata["preprocessing"]["pad_token_id"])
    all_logits, embeddings = [], []
    for start in range(0, len(rows), TEXT_BATCH):
        ids, mask = _pad(rows[start : start + TEXT_BATCH], pad_id)
        logits, embedding = model.session.run(None, {"input_ids": ids, "attention_mask": mask})
        all_logits.append(np.asarray(logits))
        embeddings.append(np.asarray(embedding))
    return np.concatenate(all_logits) / _temperature(model), np.concatenate(embeddings)


def clean_text(text: str) -> str:
    """Validate and normalize a free-text input."""
    text = text.strip()
    if not text:
        raise InvalidInputError("Text is empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise InvalidInputError(f"Text is longer than {MAX_TEXT_CHARS} characters.")
    return text


def score_text(model: LoadedModel, text: str) -> Scored:
    """Class probabilities + embedding of one text."""
    assert model.tokenizer is not None
    logits, embedding = _run_text(model, [model.tokenizer.encode(text).ids])
    return Scored(logits=logits[0], embedding=embedding[0])


def _coalition_count(n_words: int) -> int:
    return min(max(48, 3 * n_words), 192)


def banzhaf_weights(keep: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    """Least-squares fit outcomes ~ keep @ w + b (tiny ridge for stability); w per word."""
    design = np.hstack([keep.astype(np.float64), np.ones((len(keep), 1))])
    ridge = 1e-3 * np.eye(design.shape[1])
    ridge[-1, -1] = 0.0  # never shrink the intercept
    coef = np.linalg.solve(design.T @ design + ridge, design.T @ outcomes)
    return coef[:-1]


def explain_text(model: LoadedModel, text: str, target: int) -> dict[str, Any]:
    """Banzhaf-value word attributions (random-coalition [MASK]ing) for class `target`."""
    assert model.tokenizer is not None
    encoding = model.tokenizer.encode(text)
    mask_id = model.metadata["preprocessing"].get("mask_token_id")
    words = [(m.group(), m.start(), m.end()) for m in _WORD.finditer(text)]
    # Sub-word token positions per word (special tokens have empty offsets).
    token_spans = [
        (i, start, end)
        for i, (start, end) in enumerate(encoding.offsets)
        if end > start and encoding.special_tokens_mask[i] == 0
    ]
    word_tokens = [[i for i, s, e in token_spans if s < w_end and e > w_start] for _, w_start, w_end in words]
    # Words truncated away (beyond max_length) have no tokens — no influence to measure.
    scored = [w for w, positions in enumerate(word_tokens) if positions]
    weights: dict[int, float] = {}
    if scored:
        # Seeded by the text itself: the same input always gets the same explanation.
        rng = np.random.default_rng(int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big"))
        keep = rng.random((_coalition_count(len(scored)), len(scored))) < 0.5
        keep[0] = True  # the full text
        keep[1] = False  # everything masked
        variants = []
        for row in keep:
            ids = list(encoding.ids)
            dropped = {p for k, w in zip(row, scored, strict=True) if not k for p in word_tokens[w]}
            if mask_id is not None:
                ids = [int(mask_id) if i in dropped else t for i, t in enumerate(ids)]
            else:  # no [MASK] in the vocabulary — drop the tokens instead
                ids = [t for i, t in enumerate(ids) if i not in dropped]
            variants.append(ids)
        logits, _ = _run_text(model, variants)
        coefficients = banzhaf_weights(keep, log_odds(logits, target))
        weights = {w: float(v) for w, v in zip(scored, coefficients, strict=True)}
    return {
        "type": "tokens",
        "method": f"Banzhaf values over {_coalition_count(len(scored))} random [MASK] coalitions (log-odds)",
        "tokens": [
            {"text": word, "start": start, "end": end, "weight": round(weights[w], 5) if w in weights else None}
            for w, (word, start, end) in enumerate(words)
        ],
    }


# ── image ─────────────────────────────────────────────────────────────────────


def decode_image(data: bytes, preprocessing: dict[str, Any]) -> np.ndarray:
    """Untrusted image bytes -> uint8 array exactly as dl-training fed the model
    (source mode, square resize).
    """
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidInputError(f"Image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            converted = img.convert(preprocessing["source_mode"])
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise InvalidInputError(f"Not a readable image: {exc}") from exc
    size = int(preprocessing["size"])
    return np.asarray(converted.resize((size, size), Image.Resampling.BILINEAR), dtype=np.uint8)


def decode_base64_image(payload: str, preprocessing: dict[str, Any]) -> np.ndarray:
    """A base64 (optionally data-URL) image."""
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    try:
        data = base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise InvalidInputError("image_base64 is not valid base64.") from exc
    return decode_image(data, preprocessing)


def to_pixels(images: np.ndarray, preprocessing: dict[str, Any]) -> np.ndarray:
    """uint8 (N, H, W[, 3]) -> normalized float32 (N, 3, H, W) — mirrors dl-training."""
    x = images.astype(np.float32) / 255.0
    x = np.repeat(x[:, None, :, :], 3, axis=1) if x.ndim == 3 else x.transpose(0, 3, 1, 2)
    mean = np.asarray(preprocessing["mean"], dtype=np.float32).reshape(1, 3, 1, 1)
    std = np.asarray(preprocessing["std"], dtype=np.float32).reshape(1, 3, 1, 1)
    return (x - mean) / std


def has_anomaly_map(model: LoadedModel) -> bool:
    """Whether the deployed graph is an anomaly detector (third `anomaly_map` output)."""
    return any(output.name == ONNX_ANOMALY_MAP_OUTPUT for output in model.session.get_outputs())


def _run_images(model: LoadedModel, images: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    with_map = has_anomaly_map(model)
    names = [ONNX_LOGITS_OUTPUT, ONNX_EMBEDDING_OUTPUT] + ([ONNX_ANOMALY_MAP_OUTPUT] if with_map else [])
    # An anomaly detector's kNN matrix (patches x memory bank) is large per image: one at a time.
    batch = 1 if with_map else OCCLUSION_BATCH
    all_logits, embeddings, maps = [], [], []
    for start in range(0, len(images), batch):
        pixels = to_pixels(images[start : start + batch], model.metadata["preprocessing"])
        outputs = model.session.run(names, {ONNX_IMAGE_INPUT: pixels})
        all_logits.append(np.asarray(outputs[0]))
        embeddings.append(np.asarray(outputs[1]))
        if with_map:
            maps.append(np.asarray(outputs[2]))
    anomaly_maps = np.concatenate(maps) if with_map else None
    return np.concatenate(all_logits) / _temperature(model), np.concatenate(embeddings), anomaly_maps


def score_image(model: LoadedModel, image: np.ndarray) -> Scored:
    """Class probabilities + embedding (+ anomaly map) of one preprocessed uint8 image."""
    logits, embedding, maps = _run_images(model, image[None])
    return Scored(logits=logits[0], embedding=embedding[0], anomaly_map=None if maps is None else maps[0])


def explain_image(model: LoadedModel, image: np.ndarray, target: int, base_logits: np.ndarray) -> dict[str, Any]:
    """Occlusion-sensitivity heatmap (grid x grid) for class `target`."""
    grid = int(model.metadata.get("occlusion_grid", 8))
    h, w = image.shape[:2]
    ph, pw = math.ceil(h / grid), math.ceil(w / grid)
    fill = image.mean(axis=(0, 1)).astype(np.uint8)
    variants = np.repeat(image[None], grid * grid, axis=0)
    for r in range(grid):
        for c in range(grid):
            variants[r * grid + c, r * ph : (r + 1) * ph, c * pw : (c + 1) * pw] = fill
    logits, _, _ = _run_images(model, variants)
    base = float(log_odds(base_logits[None], target)[0])
    drops = (base - log_odds(logits, target)).reshape(grid, grid)
    return {
        "type": "heatmap",
        "method": f"occlusion sensitivity ({grid}x{grid} patches), log-odds drop",
        "grid": [[round(float(v), 5) for v in row] for row in drops],
    }


def explain_anomaly(model: LoadedModel, anomaly_map: np.ndarray) -> dict[str, Any]:
    """The detector's own patch map, on a fixed scale: 0 up to `map_floor` (what 99% of
    patches of unseen *normal* parts reach), 1 at `map_ceiling` (a typical defect's
    peak) — so a good part shows no heat at all, instead of its least-typical patch
    being painted as hot as a real defect would be.
    """
    info = model.metadata.get("anomaly") or {}
    floor = float(info.get("map_floor", 0.0))
    ceiling = max(float(info.get("map_ceiling", float(anomaly_map.max()) or 1.0)), floor + 1e-6)
    grid = np.clip((anomaly_map - floor) / (ceiling - floor), 0.0, 1.0)
    return {
        "type": "heatmap",
        "method": "patch anomaly map — distance of each patch to its nearest normal patch",
        "grid": [[round(float(v), 4) for v in row] for row in grid],
        "vmax": 1.0,
        "peak_distance": round(float(anomaly_map.max()), 5),
    }


def png_base64(image: np.ndarray) -> str:
    """The exact pixels the model saw, for the UI to overlay the heatmap on."""
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


# ── shared ────────────────────────────────────────────────────────────────────


def similar_cases(model: LoadedModel, embedding: np.ndarray, k: int) -> list[dict[str, Any]]:
    """Top-k reference (training) samples by cosine similarity of the model's embedding."""
    if k <= 0 or len(model.reference_embeddings) == 0:
        return []
    query = embedding / max(float(np.linalg.norm(embedding)), 1e-12)
    similarity = model.reference_embeddings @ query
    top = np.argsort(-similarity)[:k]
    ref = model.reference
    texts = ref.get("texts")
    return [
        {
            "id": ref["ids"][i],
            "label": ref["labels"][i],
            "similarity": round(float(similarity[i]), 4),
            **({"text": texts[i]} if texts else {}),
        }
        for i in top
    ]
