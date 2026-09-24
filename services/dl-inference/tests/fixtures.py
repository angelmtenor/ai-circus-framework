"""Real-but-tiny deep-learning artifacts for dl-inference's tests — no network, no torch.

Builds genuine ONNX graphs with onnx.helper (same I/O contract dl-training exports:
`logits` + `embedding`), a genuine `tokenizers` WordLevel tokenizer with [MASK], and
the manifest/checksum layout of ai_circus_shared.deep_learning, stored in an in-memory
ObjectStore stand-in — so onnxruntime, checksum verification and the explanations run
for real in tests.
"""

from __future__ import annotations

import io
import json
from typing import Any

import numpy as np
import onnx
from ai_circus_shared.deep_learning import (
    DL_METADATA_KEY,
    DL_MODEL_KEY,
    DL_REFERENCE_EMBEDDINGS_KEY,
    DL_REFERENCE_KEY,
    DL_SAMPLES_KEY,
    DL_TOKENIZER_KEY,
    image_key,
)
from ai_circus_shared.tabular_ml import artifact_checksum
from onnx import TensorProto, helper, numpy_helper
from PIL import Image
from tokenizers import Tokenizer, models, pre_tokenizers, processors

VOCAB = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "fever", "rash", "sneezing", "nose", "i", "have", "a", "and"]
TEXT_LABELS = [{"key": "dengue", "label": "Dengue"}, {"key": "common cold", "label": "Common cold"}]
IMAGE_LABELS = [{"key": "0", "label": "Normal"}, {"key": "1", "label": "Pneumonia"}]
IMAGE_SIZE = 16


class MemoryStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore (same tenant-key API)."""

    def __init__(self, bucket: str = "test-bucket") -> None:
        """Empty store."""
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.gets: list[str] = []

    def put(self, org_id: str, path: str, data: bytes) -> str:
        """Store bytes under the tenant prefix."""
        key = f"tenant-{org_id}/{path}"
        self.objects[key] = data
        return key

    def get(self, org_id: str, path: str) -> bytes:
        """Read bytes (KeyError if absent, like a real 404)."""
        key = f"tenant-{org_id}/{path}"
        self.gets.append(key)
        return self.objects[key]

    def exists(self, org_id: str, path: str) -> bool:
        """Whether an object exists."""
        return f"tenant-{org_id}/{path}" in self.objects


def text_tokenizer() -> Tokenizer:
    """WordLevel tokenizer with BERT-style [CLS] … [SEP] post-processing and offsets."""
    tokenizer = Tokenizer(models.WordLevel({w: i for i, w in enumerate(VOCAB)}, unk_token="[UNK]"))  # ruff: ignore[hardcoded-password-func-arg]
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.normalizer = None
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)]
    )
    tokenizer.enable_truncation(32)
    return tokenizer


def _text_onnx() -> bytes:
    """Embedding lookup -> masked mean -> linear head. "fever"/"rash" push class 0
    (dengue), "sneezing"/"nose" push class 1 — so explanations have a known answer.
    """
    dim = 4
    table = np.zeros((len(VOCAB), dim), dtype=np.float32)
    table[VOCAB.index("fever"), 0] = 3.0
    table[VOCAB.index("rash"), 0] = 2.0
    table[VOCAB.index("sneezing"), 1] = 3.0
    table[VOCAB.index("nose"), 1] = 2.0
    head = np.array([[1, -1], [-1, 1], [0, 0], [0, 0]], dtype=np.float32)
    nodes = [
        helper.make_node("Gather", ["table", "input_ids"], ["emb"]),
        helper.make_node("Cast", ["attention_mask"], ["maskf"], to=TensorProto.FLOAT),
        helper.make_node("Unsqueeze", ["maskf", "axis2"], ["mask3"]),
        helper.make_node("Mul", ["emb", "mask3"], ["masked"]),
        helper.make_node("ReduceSum", ["masked", "axis1"], ["summed"], keepdims=0),
        helper.make_node("ReduceSum", ["maskf", "axis1"], ["count"], keepdims=1),
        helper.make_node("Div", ["summed", "count"], ["pooled"]),
        helper.make_node("MatMul", ["pooled", "head"], ["logits_raw"]),
        helper.make_node("Mul", ["logits_raw", "scale"], ["logits"]),
        helper.make_node("LpNormalization", ["pooled"], ["embedding"], axis=1, p=2),
    ]
    graph = helper.make_graph(
        nodes,
        "tiny-text",
        [
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["batch", "sequence"]),
            helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["batch", "sequence"]),
        ],
        [
            helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", 2]),
            helper.make_tensor_value_info("embedding", TensorProto.FLOAT, ["batch", dim]),
        ],
        initializer=[
            numpy_helper.from_array(table, "table"),
            numpy_helper.from_array(head, "head"),
            numpy_helper.from_array(np.array([4.0], dtype=np.float32), "scale"),
            numpy_helper.from_array(np.array([2], dtype=np.int64), "axis2"),
            numpy_helper.from_array(np.array([1], dtype=np.int64), "axis1"),
        ],
    )
    return onnx.helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)], ir_version=10).SerializeToString()


def _image_onnx() -> bytes:
    """Mean brightness of the top-left quadrant drives "pneumonia" — a known hotspot."""
    half = IMAGE_SIZE // 2
    nodes = [
        helper.make_node("Slice", ["pixel_values", "starts", "ends", "axes"], ["quadrant"]),
        helper.make_node("ReduceMean", ["quadrant", "hw"], ["q"], keepdims=0),  # (b, 3)
        helper.make_node("ReduceMean", ["pixel_values", "hw"], ["pooled"], keepdims=0),  # (b, 3)
        helper.make_node("ReduceMean", ["q", "c"], ["qm"], keepdims=1),  # (b, 1)
        helper.make_node("Mul", ["qm", "gain"], ["pos"]),
        helper.make_node("Neg", ["pos"], ["neg"]),
        helper.make_node("Concat", ["neg", "pos"], ["logits"], axis=1),
        helper.make_node("LpNormalization", ["pooled"], ["embedding"], axis=1, p=2),
    ]
    graph = helper.make_graph(
        nodes,
        "tiny-image",
        [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, ["batch", 3, IMAGE_SIZE, IMAGE_SIZE])],
        [
            helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", 2]),
            helper.make_tensor_value_info("embedding", TensorProto.FLOAT, ["batch", 3]),
        ],
        initializer=[
            numpy_helper.from_array(np.array([0, 0], dtype=np.int64), "starts"),
            numpy_helper.from_array(np.array([half, half], dtype=np.int64), "ends"),
            numpy_helper.from_array(np.array([2, 3], dtype=np.int64), "axes"),
            numpy_helper.from_array(np.array([2, 3], dtype=np.int64), "hw"),
            numpy_helper.from_array(np.array([1], dtype=np.int64), "c"),
            numpy_helper.from_array(np.array([2.0], dtype=np.float32), "gain"),
        ],
    )
    return onnx.helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)], ir_version=10).SerializeToString()


def png(image: np.ndarray) -> bytes:
    """uint8 array -> PNG bytes."""
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG")
    return buffer.getvalue()


def bright_quadrant_image() -> np.ndarray:
    """A 16x16 grayscale image, bright only in its top-left quadrant."""
    image = np.zeros((IMAGE_SIZE, IMAGE_SIZE), dtype=np.uint8)
    image[: IMAGE_SIZE // 2, : IMAGE_SIZE // 2] = 255
    return image


def _embeddings(rows: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, rows.astype(np.float16), allow_pickle=False)
    return buffer.getvalue()


def publish(store: MemoryStore, org_id: str, modality: str, **metadata_overrides: Any) -> dict[str, Any]:
    """Write a complete, checksummed artifact set (manifest last) for one tiny model."""
    if modality == "text":
        blobs = {
            "model": (DL_MODEL_KEY, _text_onnx()),
            "tokenizer": (DL_TOKENIZER_KEY, text_tokenizer().to_str().encode()),
            "samples": (
                DL_SAMPLES_KEY,
                json.dumps({
                    "samples": [
                        {"id": "s-0", "label": "dengue", "probs": [0.9, 0.1], "text": "i have fever and rash"},
                        {"id": "s-1", "label": "common cold", "probs": [0.2, 0.8], "text": "sneezing and nose"},
                    ]
                }).encode(),
            ),
            "reference": (
                DL_REFERENCE_KEY,
                json.dumps({
                    "ids": ["r-0", "r-1"],
                    "labels": ["dengue", "common cold"],
                    "texts": ["fever", "nose"],
                }).encode(),
            ),
            "reference_embeddings": (DL_REFERENCE_EMBEDDINGS_KEY, _embeddings(np.array([[1, 0, 0, 0], [0, 1, 0, 0]]))),
        }
        preprocessing = {"max_length": 32, "pad_token_id": 0, "mask_token_id": 4}
        labels = TEXT_LABELS
    else:
        blobs = {
            "model": (DL_MODEL_KEY, _image_onnx()),
            "samples": (
                DL_SAMPLES_KEY,
                json.dumps({"samples": [{"id": "s-0", "label": "1", "probs": [0.1, 0.9]}]}).encode(),
            ),
            "reference": (DL_REFERENCE_KEY, json.dumps({"ids": ["r-0"], "labels": ["1"], "texts": None}).encode()),
            "reference_embeddings": (
                DL_REFERENCE_EMBEDDINGS_KEY,
                _embeddings(np.array([[1.0, 1.0, 1.0]]) / np.sqrt(3)),
            ),
        }
        store.put(org_id, image_key("s-0"), png(bright_quadrant_image()))
        store.put(org_id, image_key("r-0"), png(bright_quadrant_image()))
        preprocessing = {"size": IMAGE_SIZE, "mean": [0.5, 0.5, 0.5], "std": [0.5, 0.5, 0.5], "source_mode": "L"}
        labels = IMAGE_LABELS
    checksums = {}
    for name, (key, data) in blobs.items():
        store.put(org_id, key, data)
        checksums[name] = artifact_checksum(data)
    metadata = {
        "modality": modality,
        "labels": labels,
        "preprocessing": preprocessing,
        "occlusion_grid": 4,
        "base_model": "tiny/test",
        "evaluation": {"metrics": {"accuracy": 1.0}},
        "checksums": checksums,
        **metadata_overrides,
    }
    store.put(org_id, DL_METADATA_KEY, json.dumps(metadata).encode())
    return metadata
