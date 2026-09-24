"""End-to-end: download (cached) -> fine-tune -> ONNX export (+int8) -> evaluate the
exported model -> publish checksummed artifacts, for both modalities, with tiny models.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
import pytest
from ai_circus_shared.deep_learning import (
    DL_METADATA_KEY,
    DL_MODEL_KEY,
    DL_REFERENCE_EMBEDDINGS_KEY,
    DL_REFERENCE_KEY,
    DL_SAMPLES_KEY,
    DL_TOKENIZER_KEY,
)
from ai_circus_shared.tabular_ml import artifact_checksum
from tokenizers import Tokenizer

from dl_training.core import pipeline
from dl_training.core.device import resolve_device
from tests.fixtures import (
    MemoryStore,
    image_config,
    image_npz_bytes,
    scenario,
    seed_cache,
    text_config,
    text_raw_files,
    tiny_task,
)


def _key(path: str) -> str:
    return f"tenant-demo/{path}"


def test_text_scenario_end_to_end(tmp_path: Path) -> None:
    store = MemoryStore()
    seed_cache(tmp_path, text_raw_files())
    manifest = pipeline.train_scenario(
        scenario(text_config()),
        store,  # type: ignore[arg-type]
        org_id="demo",
        device=resolve_device("cpu"),
        cache_dir=tmp_path,
        task_builder=tiny_task,
    )

    # The pinned raw files were uploaded to the tenant's prefix.
    assert store.objects[_key("raw/train.jsonl")] == text_raw_files()["train.jsonl"]
    # Manifest: device/budget/evaluation of the deployed int8 artifact, checksums of every artifact.
    assert manifest["device"]["kind"] == "cpu"
    assert manifest["budget_kind"] == "cpu"
    assert manifest["quantized"] is True
    assert manifest["train_size"] == 22 and manifest["val_size"] == 2 and manifest["test_size"] == 6
    assert len(manifest["history"]) == 2
    assert manifest["evaluation"]["n"] == 6
    assert {"accuracy", "macro_f1", "ece"} <= set(manifest["evaluation"]["metrics"])
    assert manifest["preprocessing"]["mask_token_id"] == 4
    assert manifest["temperature"] > 0
    assert manifest["label_smoothing"] == pytest.approx(0.1)
    assert "ece_uncalibrated" in manifest["evaluation"]["metrics"]
    stored = json.loads(store.objects[_key(DL_METADATA_KEY)])
    assert stored == manifest
    for name, path in {
        "model": DL_MODEL_KEY,
        "tokenizer": DL_TOKENIZER_KEY,
        "samples": DL_SAMPLES_KEY,
        "reference": DL_REFERENCE_KEY,
        "reference_embeddings": DL_REFERENCE_EMBEDDINGS_KEY,
    }.items():
        assert manifest["checksums"][name] == artifact_checksum(store.objects[_key(path)])

    samples = json.loads(store.objects[_key(DL_SAMPLES_KEY)])["samples"]
    assert len(samples) == 4 and all("text" in s and len(s["probs"]) == 2 for s in samples)
    reference = json.loads(store.objects[_key(DL_REFERENCE_KEY)])
    embeddings = np.load(io.BytesIO(store.objects[_key(DL_REFERENCE_EMBEDDINGS_KEY)]))
    assert embeddings.shape == (6, 16) and len(reference["texts"]) == 6
    assert np.allclose(np.linalg.norm(embeddings.astype(np.float32), axis=1), 1.0, atol=1e-2)

    # The published graph + tokenizer run in onnxruntime exactly the way dl-inference uses them.
    session = ort.InferenceSession(store.objects[_key(DL_MODEL_KEY)], providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_str(store.objects[_key(DL_TOKENIZER_KEY)].decode())
    ids = np.array([tokenizer.encode("fever rash").ids], dtype=np.int64)
    logits, embedding = session.run(None, {"input_ids": ids, "attention_mask": np.ones_like(ids)})
    assert logits.shape == (1, 2) and embedding.shape == (1, 16)


def test_image_scenario_end_to_end(tmp_path: Path) -> None:
    store = MemoryStore()
    seed_cache(tmp_path, {"tiny.npz": image_npz_bytes()})
    manifest = pipeline.train_scenario(
        scenario(image_config()),
        store,  # type: ignore[arg-type]
        org_id="demo",
        device=resolve_device("cpu"),
        cache_dir=tmp_path,
        task_builder=tiny_task,
    )
    assert manifest["modality"] == "image"
    assert manifest["quantized"] is False
    assert manifest["preprocessing"] == {"size": 32, "mean": [0.5] * 3, "std": [0.5] * 3, "source_mode": "L"}
    assert "auroc" in manifest["evaluation"]["metrics"]
    assert "roc_curve" in manifest["evaluation"]
    assert manifest["occlusion_grid"] == 4
    pngs = [k for k in store.objects if k.startswith(_key("images/"))]
    assert len(pngs) == 4 + 6  # gallery + reference
    assert all(store.objects[k].startswith(b"\x89PNG") for k in pngs)
    assert "tokenizer" not in manifest["checksums"]


def test_a_failing_digest_stops_the_pipeline_before_training(tmp_path: Path) -> None:
    from dl_training.core.data import DataIntegrityError

    def fake_download(url: str, dest: Path) -> None:
        dest.write_bytes(b"tampered")

    with pytest.raises(DataIntegrityError):
        from dl_training.core import data

        data.ensure_raw(MemoryStore(), "demo", text_config(), tmp_path, download=fake_download)  # type: ignore[arg-type]
