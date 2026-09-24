"""
- Title:    Upload a trained deep_learning model's artifacts (tenant-scoped, checksummed)
- Author:   ai-circus-framework contributors

Writes the ai_circus_shared.deep_learning contract: images first, then the model and
its side files, and metadata.json *last* with every checksummed artifact's SHA-256 — a
run interrupted half-way leaves a manifest that no longer matches, which dl-inference
refuses to load, instead of a silent mix of two runs' files.
"""

from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from ai_circus_shared.deep_learning import (
    DL_METADATA_KEY,
    DL_MODEL_KEY,
    DL_REFERENCE_EMBEDDINGS_KEY,
    DL_REFERENCE_KEY,
    DL_SAMPLES_KEY,
    DL_TOKENIZER_KEY,
    gallery_sample_id,
    image_key,
    reference_sample_id,
)
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import artifact_checksum
from PIL import Image

from dl_training.core.data import Split
from dl_training.core.logger import get_logger

logger = get_logger(__name__)

UPLOAD_ATTEMPTS = 5


def put_with_retry(store: ObjectStore, org_id: str, key: str, data: bytes, attempts: int = UPLOAD_ATTEMPTS) -> None:
    """Upload with exponential backoff — a finished training run must not be thrown away
    because SeaweedFS (or the host's tunnel to it) blinked for a few seconds.
    """
    for attempt in range(1, attempts + 1):
        try:
            store.put(org_id, key, data)
            return
        except Exception:
            if attempt == attempts:
                raise
            delay = 2**attempt
            logger.warning("Upload of {} failed (attempt {}/{}) — retrying in {}s", key, attempt, attempts, delay)
            time.sleep(delay)


def png_bytes(image: np.ndarray) -> bytes:
    """uint8 (H, W) / (H, W, 3) -> PNG bytes."""
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


@dataclass(frozen=True)
class Published:
    """Held-out gallery + indexed reference set, ready to upload."""

    gallery: Split
    gallery_probs: np.ndarray
    reference: Split
    reference_embeddings: np.ndarray


def samples_document(published: Published, label_keys: list[str]) -> dict[str, Any]:
    """samples.json — each gallery sample's true label and the deployed model's probabilities."""
    samples = []
    for i in range(len(published.gallery)):
        sample: dict[str, Any] = {
            "id": gallery_sample_id(i),
            "label": label_keys[int(published.gallery.labels[i])],
            "probs": [round(float(p), 5) for p in published.gallery_probs[i]],
        }
        if isinstance(published.gallery.inputs, list):
            sample["text"] = published.gallery.inputs[i]
        samples.append(sample)
    return {"samples": samples}


def reference_document(published: Published, label_keys: list[str]) -> dict[str, Any]:
    """reference.json — row i describes row i of reference_embeddings.npy."""
    ref = published.reference
    return {
        "ids": [reference_sample_id(i) for i in range(len(ref))],
        "labels": [label_keys[int(label)] for label in ref.labels],
        "texts": ref.inputs if isinstance(ref.inputs, list) else None,
    }


def upload(
    store: ObjectStore,
    org_id: str,
    *,
    model_path: Path,
    tokenizer_path: Path | None,
    published: Published,
    label_keys: list[str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Upload everything; returns the final metadata (with checksums) as written."""
    for split, make_id in ((published.gallery, gallery_sample_id), (published.reference, reference_sample_id)):
        if isinstance(split.inputs, np.ndarray):
            for i, image in enumerate(split.inputs):
                put_with_retry(store, org_id, image_key(make_id(i)), png_bytes(image))
    if isinstance(published.gallery.inputs, np.ndarray):
        logger.info("Uploaded {} gallery + {} reference images", len(published.gallery), len(published.reference))

    embeddings = io.BytesIO()
    np.save(embeddings, published.reference_embeddings.astype(np.float16), allow_pickle=False)
    blobs: dict[str, tuple[str, bytes]] = {
        "model": (DL_MODEL_KEY, model_path.read_bytes()),
        "samples": (DL_SAMPLES_KEY, json.dumps(samples_document(published, label_keys)).encode()),
        "reference": (DL_REFERENCE_KEY, json.dumps(reference_document(published, label_keys)).encode()),
        "reference_embeddings": (DL_REFERENCE_EMBEDDINGS_KEY, embeddings.getvalue()),
    }
    if tokenizer_path is not None:
        blobs["tokenizer"] = (DL_TOKENIZER_KEY, tokenizer_path.read_bytes())

    checksums = {}
    for name, (key, data) in blobs.items():
        put_with_retry(store, org_id, key, data)
        checksums[name] = artifact_checksum(data)
    final = {**metadata, "model_size_mb": round(len(blobs["model"][1]) / 1e6, 1), "checksums": checksums}
    put_with_retry(store, org_id, DL_METADATA_KEY, json.dumps(final, indent=2).encode())
    logger.success("Uploaded model artifacts + manifest (org={}, bucket={})", org_id, store.bucket)
    return final
