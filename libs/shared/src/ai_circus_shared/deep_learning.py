"""Artifact contract between `dl-training` (writer) and `dl-inference` (reader) for
`deep_learning` scenarios — the DL counterpart of `tabular_ml.py`'s MODEL_* keys.

Every key below is a path *inside* one tenant's prefix of the scenario's SeaweedFS
bucket (see `storage.ObjectStore._key`), so artifacts are org-scoped exactly like the
tabular ones. `DL_METADATA_KEY` is the manifest: dl-training writes it last, once every
other artifact is uploaded, with each artifact's SHA-256 under `checksums` — so
dl-inference can refuse a half-overwritten retrain instead of mixing old and new files.

Deliberately dependency-free (no numpy/torch/onnxruntime): both services import it,
and the inference side must stay a lean onnxruntime-only image.
"""

from __future__ import annotations

import re

# Raw public data as downloaded (text: the source's own JSONL files; image: the .npz).
DL_RAW_PREFIX = "raw/"

# The deployed model: ONNX graph with outputs `logits` and `embedding` (see below).
DL_MODEL_KEY = "model/model.onnx"
# Text only: the Hugging Face `tokenizers` JSON the model was trained with.
DL_TOKENIZER_KEY = "model/tokenizer.json"
# Written last — manifest with metrics, history, device, preprocessing and checksums.
DL_METADATA_KEY = "model/metadata.json"
# Held-out test samples published for the UI, each with the deployed model's own
# precomputed class probabilities (gallery, reading-room worklist, triage stream).
DL_SAMPLES_KEY = "model/samples.json"
# Training samples indexed for similar-case retrieval: metadata JSON + float16 matrix
# (row i = embedding of reference sample i, L2-normalized), saved with numpy.save.
DL_REFERENCE_KEY = "model/reference.json"
DL_REFERENCE_EMBEDDINGS_KEY = "model/reference_embeddings.npy"
# Image only: one PNG per published sample (gallery "s-…" and reference "r-…" ids).
DL_IMAGES_PREFIX = "images/"

# Artifacts whose SHA-256 must match the manifest before dl-inference loads them.
DL_CHECKSUMMED_ARTIFACTS = {
    "model": DL_MODEL_KEY,
    "samples": DL_SAMPLES_KEY,
    "reference": DL_REFERENCE_KEY,
    "reference_embeddings": DL_REFERENCE_EMBEDDINGS_KEY,
}
DL_TEXT_ONLY_ARTIFACTS = {"tokenizer": DL_TOKENIZER_KEY}

# ONNX graph I/O names — fixed so the exporter and the runtime can't drift apart.
ONNX_TEXT_INPUTS = ("input_ids", "attention_mask")
ONNX_IMAGE_INPUT = "pixel_values"
ONNX_LOGITS_OUTPUT = "logits"
ONNX_EMBEDDING_OUTPUT = "embedding"

_SAFE_SAMPLE_ID = re.compile(r"^[sr]-[0-9]{1,6}$")


def gallery_sample_id(index: int) -> str:
    """Id of the `index`-th published held-out (test) sample."""
    return f"s-{index}"


def reference_sample_id(index: int) -> str:
    """Id of the `index`-th indexed training (reference) sample."""
    return f"r-{index}"


def image_key(sample_id: str) -> str:
    """Object key of one published sample's PNG — rejects anything but a real sample id,
    since dl-inference builds this from a request path parameter.
    """
    if not _SAFE_SAMPLE_ID.match(sample_id):
        raise ValueError(f"Invalid sample id {sample_id!r}.")
    return f"{DL_IMAGES_PREFIX}{sample_id}.png"
