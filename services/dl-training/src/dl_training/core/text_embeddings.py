"""
- Title:    Sentence embeddings of a tabular_ml scenario's free text, on this machine's GPU
- Author:   Angel Martinez-Tenor

A `tabular_ml` scenario with a `model.text_challenger` (see
ai_circus_shared.scenario_schema.TextChallenger) compares its TF-IDF model against the
same features read by a sentence-transformer. Its training job gets those vectors from
llm-gateway (`local-embed`), which runs on CPU in the cluster — ~0.6 s per review, far
too slow for a whole dataset. This step loads the very same weights (`hf_model_id`) on
the host GPU, embeds every text of the tenant's normalized dataset in seconds and
writes an .npz cache next to the model artifacts; training reads it and only asks the
gateway for what's missing (and to verify the two agree).
"""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

import numpy as np
import pyarrow.parquet as pq
from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import NORMALIZED_DATASET_KEY, text_embedding_cache_key, text_hash

from dl_training.core.device import DeviceInfo
from dl_training.core.logger import get_logger

logger = get_logger(__name__)

BATCH_SIZE = 64
# Builds a sentence encoder: (hf_model_id, torch device string) -> object with
# .encode(list[str], ...) -> np.ndarray. Injectable so tests don't load a real model.
EncoderFactory = Callable[[str, str], Any]


def _sentence_transformer(model_id: str, device: str) -> Any:
    from sentence_transformers import SentenceTransformer  # heavy: torch + transformers

    return SentenceTransformer(model_id, device=device)


def load_cache(store: ObjectStore, org_id: str, embedding_model: str) -> dict[str, Any] | None:
    """The tenant's existing cache (`hashes`, `vectors`, `hf_model_id`), or None."""
    key = text_embedding_cache_key(embedding_model)
    if not store.exists(org_id, key):
        return None
    with np.load(io.BytesIO(store.get(org_id, key)), allow_pickle=False) as data:
        return {"hashes": list(data["hashes"]), "vectors": data["vectors"], "hf_model_id": str(data["hf_model_id"])}


def embed_scenario_texts(
    definition: ScenarioDefinition,
    store: ObjectStore,
    org_id: str,
    device: DeviceInfo,
    encoder_factory: EncoderFactory = _sentence_transformer,
) -> dict[str, Any]:
    """Embed every distinct text of the scenario's text columns (skipping ones already
    cached with the same model) and write the merged cache; return a small summary."""
    assert definition.dataset is not None and definition.model is not None
    challenger = definition.model.text_challenger
    assert challenger is not None
    table = pq.read_table(io.BytesIO(store.get(org_id, NORMALIZED_DATASET_KEY)))
    # etl-tabular already filled missing text with "" — `or ""` just guards older data.
    texts = sorted({str(t or "") for column in definition.dataset.text_columns() for t in table.column(column).to_pylist()})

    cache = load_cache(store, org_id, challenger.embedding_model)
    if cache is not None and cache["hf_model_id"] != challenger.hf_model_id:
        logger.warning("Cache was built with {} — rebuilding for {}", cache["hf_model_id"], challenger.hf_model_id)
        cache = None
    known = dict(zip(cache["hashes"], cache["vectors"], strict=True)) if cache else {}
    missing = [t for t in texts if text_hash(t) not in known]
    if missing:
        logger.info("Embedding {} texts with {} on {} ({})", len(missing), challenger.hf_model_id, device.kind, device.name)
        encoder = encoder_factory(challenger.hf_model_id, device.kind)
        # normalize_embeddings=True: exactly what llm-gateway's local-embed handler returns.
        vectors = np.asarray(
            encoder.encode(missing, batch_size=BATCH_SIZE, normalize_embeddings=True, show_progress_bar=False),
            dtype=np.float32,
        )
        known.update(zip((text_hash(t) for t in missing), vectors, strict=True))

    hashes = sorted(known)
    buffer = io.BytesIO()
    np.savez_compressed(
        buffer,
        hashes=np.asarray(hashes),
        vectors=np.stack([known[h] for h in hashes]).astype(np.float32),
        hf_model_id=np.asarray(challenger.hf_model_id),
    )
    store.put(org_id, text_embedding_cache_key(challenger.embedding_model), buffer.getvalue())
    return {"texts": len(texts), "embedded": len(missing), "cached": len(hashes), "dimension": len(next(iter(known.values())))}
