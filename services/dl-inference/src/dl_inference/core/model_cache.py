"""
- Title:    Per-(tenant, scenario) deep-learning model cache
- Author:   ai-circus-framework contributors

Same contract as prediction's ModelCache — lazy load per (org_id, scenario_slug) from
the scenario's SeaweedFS bucket, falling back to the shared baseline org until a tenant
has its own model, checksum-verified against the manifest (metadata.json, written last
by dl-training), bounded LRU, and a per-key lock against cold-start stampedes — plus one
DL-specific addition: a cached entry re-reads the small manifest at most every
`REVALIDATE_SECONDS`, and reloads when it changed. A model retrained from the admin
console or `make dl-train-*` is therefore served within a minute, no restart needed.
"""

from __future__ import annotations

import contextlib
import ctypes
import io
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import onnxruntime as ort
from ai_circus_shared.deep_learning import (
    DL_CHECKSUMMED_ARTIFACTS,
    DL_METADATA_KEY,
    DL_TEXT_ONLY_ARTIFACTS,
    ONNX_IMAGE_INPUT,
    image_key,
    mask_key,
)
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import artifact_checksum
from tokenizers import Tokenizer

from dl_inference.core.logger import get_logger

logger = get_logger(__name__)

# Each entry holds an ONNX session (15-150 MB) — keep the pod small.
MAX_CACHED_MODELS = 4
REVALIDATE_SECONDS = 60.0
MAX_CACHED_IMAGES = 256
MAX_CACHED_EXPLANATIONS = 256


class ModelUnavailableError(RuntimeError):
    """The scenario's model can't be served right now (not trained yet, or corrupt) —
    app.py maps it to a clean 503 (with CORS headers, unlike an unhandled 500).
    """


class ModelNotTrainedError(ModelUnavailableError):
    """No manifest for the tenant nor the shared fallback org — dl-training never ran."""


class CorruptArtifactError(ModelUnavailableError):
    """An artifact's bytes don't match the manifest (interrupted retrain / tampering)."""


@dataclass
class LoadedModel:
    """Everything needed to serve one (org, scenario)."""

    org_id: str  # the org the artifacts were actually loaded from (may be the fallback)
    metadata: dict[str, Any]
    manifest_checksum: str
    session: ort.InferenceSession
    tokenizer: Tokenizer | None
    samples: list[dict[str, Any]]
    reference: dict[str, Any]
    reference_embeddings: np.ndarray  # float32, L2-normalized rows
    checked_at: float = field(default_factory=time.monotonic)
    # Explanations of *published* samples, keyed by (sample_id, target class) — the
    # worklist/gallery re-open the same studies constantly, and an image explanation is
    # ~64 forward passes. Lives on the model, so a retrain drops it with the model.
    explanations: OrderedDict[tuple[str, int], dict[str, Any]] = field(default_factory=OrderedDict)
    _explanations_guard: threading.Lock = field(default_factory=threading.Lock)

    def cached_explanation(self, sample_id: str, target: int) -> dict[str, Any] | None:
        """A previously computed explanation of a published sample, if any."""
        with self._explanations_guard:
            hit = self.explanations.get((sample_id, target))
            if hit is not None:
                self.explanations.move_to_end((sample_id, target))
            return hit

    def remember_explanation(self, sample_id: str, target: int, explanation: dict[str, Any]) -> None:
        """Keep a published sample's explanation (bounded LRU)."""
        with self._explanations_guard:
            self.explanations[sample_id, target] = explanation
            while len(self.explanations) > MAX_CACHED_EXPLANATIONS:
                self.explanations.popitem(last=False)

    @property
    def samples_by_id(self) -> dict[str, dict[str, Any]]:
        """Published held-out samples keyed by id."""
        return {s["id"]: s for s in self.samples}


def _session(model_bytes: bytes, threads: int) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # The arena keeps peak activation memory allocated forever — off, to keep RSS flat.
    options.enable_cpu_mem_arena = False
    return ort.InferenceSession(model_bytes, sess_options=options, providers=["CPUExecutionProvider"])


def release_freed_memory() -> None:
    """Hand freed heap pages back to the OS (glibc only; a no-op elsewhere). onnxruntime
    frees its per-request activations, but glibc keeps the pages mapped — without this,
    one burst of explanation traffic would pin the pod's RSS at its peak forever.
    """
    with contextlib.suppress(OSError, AttributeError):
        ctypes.CDLL("libc.so.6").malloc_trim(0)


def _warm_up(session: ort.InferenceSession, metadata: dict[str, Any]) -> None:
    """One throw-away batched run at load time: onnxruntime's first calls allocate and
    pick kernels (seconds), which would otherwise land on the first user's request.
    """
    if metadata.get("modality") == "text":
        ids = np.full((4, 48), int(metadata["preprocessing"]["pad_token_id"]), dtype=np.int64)
        session.run(None, {"input_ids": ids, "attention_mask": np.ones_like(ids)})
    else:
        size = int(metadata["preprocessing"]["size"])
        batch = 1 if metadata.get("task") == "anomaly_detection" else 4  # served one image at a time
        session.run(None, {ONNX_IMAGE_INPUT: np.zeros((batch, 3, size, size), dtype=np.float32)})


class DlModelCache:
    """Lazily loads and caches `LoadedModel`s per (org_id, scenario_slug)."""

    def __init__(self, stores: dict[str, ObjectStore], fallback_org_id: str, threads: int = 2) -> None:
        """One ObjectStore per served scenario (own bucket each)."""
        self._stores = stores
        self.fallback_org_id = fallback_org_id
        self._threads = threads
        self._cache: OrderedDict[tuple[str, str], LoadedModel] = OrderedDict()
        self._images: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()
        self._guard = threading.Lock()
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    def _lock_for(self, key: tuple[str, str]) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def store_for(self, scenario_slug: str) -> ObjectStore:
        """The scenario's bucket."""
        return self._stores[scenario_slug]

    def cached_keys(self) -> list[tuple[str, str]]:
        """(org_id, scenario_slug) of every model currently in memory."""
        with self._guard:
            return list(self._cache)

    def _source_org(self, store: ObjectStore, org_id: str, scenario_slug: str) -> str:
        if store.exists(org_id, DL_METADATA_KEY):
            return org_id
        if store.exists(self.fallback_org_id, DL_METADATA_KEY):
            return self.fallback_org_id
        raise ModelNotTrainedError(
            f"No trained deep-learning model for scenario={scenario_slug!r} (org={org_id!r}, fallback "
            f"org={self.fallback_org_id!r}) — run `make dl-train` or the admin console's Train button."
        )

    def _load(self, store: ObjectStore, source_org: str, manifest: bytes) -> LoadedModel:
        metadata = json.loads(manifest)
        checksums: dict[str, str] = metadata.get("checksums", {})
        wanted = dict(DL_CHECKSUMMED_ARTIFACTS)
        if metadata.get("modality") == "text":
            wanted |= DL_TEXT_ONLY_ARTIFACTS
        blobs: dict[str, bytes] = {}
        for name, key in wanted.items():
            data = store.get(source_org, key)
            if checksums.get(name) != artifact_checksum(data):
                raise CorruptArtifactError(f"Checksum mismatch for {name!r} (org={source_org}, key={key}).")
            blobs[name] = data
        embeddings = np.load(io.BytesIO(blobs["reference_embeddings"]), allow_pickle=False).astype(np.float32)
        tokenizer = Tokenizer.from_str(blobs["tokenizer"].decode()) if "tokenizer" in blobs else None
        session = _session(blobs["model"], self._threads)
        _warm_up(session, metadata)
        return LoadedModel(
            org_id=source_org,
            metadata=metadata,
            manifest_checksum=artifact_checksum(manifest),
            session=session,
            tokenizer=tokenizer,
            samples=json.loads(blobs["samples"])["samples"],
            reference=json.loads(blobs["reference"]),
            reference_embeddings=embeddings,
        )

    def get(self, org_id: str, scenario_slug: str) -> LoadedModel:
        """The tenant's model for this scenario, loading/reloading as needed."""
        key = (org_id, scenario_slug)
        with self._guard:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                if time.monotonic() - cached.checked_at < REVALIDATE_SECONDS:
                    return cached

        with self._lock_for(key):
            store = self._stores[scenario_slug]
            source_org = self._source_org(store, org_id, scenario_slug)
            manifest = store.get(source_org, DL_METADATA_KEY)
            with self._guard:
                cached = self._cache.get(key)
                if cached is not None and cached.manifest_checksum == artifact_checksum(manifest):
                    cached.checked_at = time.monotonic()
                    return cached
            logger.info(
                "Loading deep-learning model org={} scenario={} (from org={})", org_id, scenario_slug, source_org
            )
            loaded = self._load(store, source_org, manifest)
            release_freed_memory()  # the downloaded artifact bytes are garbage now
            with self._guard:
                self._cache[key] = loaded
                self._cache.move_to_end(key)
                while len(self._cache) > MAX_CACHED_MODELS:
                    evicted, _ = self._cache.popitem(last=False)
                    logger.info("Model cache full — evicted org={} scenario={}", *evicted)
                # A reloaded model may have new images under the same ids.
                for image in [k for k in self._images if k[:2] == key]:
                    del self._images[image]
            return loaded

    def image(self, org_id: str, scenario_slug: str, sample_id: str, *, mask: bool = False) -> bytes:
        """One published sample's PNG — or, with `mask`, its ground-truth defect mask —
        from the org the model was loaded from.
        """
        model = self.get(org_id, scenario_slug)
        cache_key = (org_id, scenario_slug, f"mask:{sample_id}" if mask else sample_id)
        with self._guard:
            if cache_key in self._images:
                self._images.move_to_end(cache_key)
                return self._images[cache_key]
        key = mask_key(sample_id) if mask else image_key(sample_id)
        data = self._stores[scenario_slug].get(model.org_id, key)
        with self._guard:
            self._images[cache_key] = data
            while len(self._images) > MAX_CACHED_IMAGES:
                self._images.popitem(last=False)
        return data
