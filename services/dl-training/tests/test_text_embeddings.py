"""Tests for the host-side sentence-embedding cache of a tabular scenario's free text."""

from __future__ import annotations

import io
from pathlib import Path
from typing import ClassVar

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.tabular_ml import NORMALIZED_DATASET_KEY, text_hash

from dl_training.core.device import DeviceInfo
from dl_training.core.text_embeddings import embed_scenario_texts, load_cache
from tests.fixtures import MemoryStore

REPO = Path(__file__).resolve().parents[3]
DEVICE = DeviceInfo(kind="cpu", name="test", torch_version="0", cuda_version=None, memory_gb=None)


class FakeEncoder:
    """Deterministic 3-d 'embeddings' (text length, vowels, 1), recording each call."""

    calls: ClassVar[list[list[str]]] = []

    def __init__(self, model_id: str, device: str) -> None:
        """Remember which model/device the factory asked for."""
        self.model_id, self.device = model_id, device

    def encode(self, texts: list[str], **kwargs: object) -> np.ndarray:
        """Record the batch and return its fake embeddings."""
        FakeEncoder.calls.append(list(texts))
        assert kwargs["normalize_embeddings"] is True
        return np.array([[len(t), sum(c in "aeiou" for c in t), 1.0] for t in texts])


def _scenario_with_data(store: MemoryStore, reviews: list[str]) -> ScenarioDefinition:
    definition = ScenarioDefinition.load(REPO / "scenarios/toxic_leadership/scenario.yaml")
    buffer = io.BytesIO()
    pq.write_table(pa.table({"Review": reviews, "CompBenefits": [3] * len(reviews)}), buffer)
    store.put("demo", NORMALIZED_DATASET_KEY, buffer.getvalue())
    return definition


def test_embeds_every_distinct_text_once_and_reuses_the_cache() -> None:
    store = MemoryStore()
    definition = _scenario_with_data(store, ["no direction", "great lead", "no direction"])
    FakeEncoder.calls = []

    summary = embed_scenario_texts(definition, store, "demo", DEVICE, encoder_factory=FakeEncoder)

    assert summary == {"texts": 2, "embedded": 2, "cached": 2, "dimension": 3}
    cache = load_cache(store, "demo", "local-embed")
    assert cache is not None and cache["hf_model_id"] == "voyageai/voyage-4-nano"
    vectors = dict(zip(cache["hashes"], cache["vectors"], strict=True))
    assert vectors[text_hash("great lead")].tolist() == [10.0, 4.0, 1.0]

    # A second run with one new review only encodes that one.
    _scenario_with_data(store, ["no direction", "great lead", "toxic politics"])
    summary = embed_scenario_texts(definition, store, "demo", DEVICE, encoder_factory=FakeEncoder)
    assert FakeEncoder.calls[-1] == ["toxic politics"]
    assert summary["cached"] == 3


def test_a_cache_built_with_another_model_is_rebuilt() -> None:
    store = MemoryStore()
    definition = _scenario_with_data(store, ["a", "b"])
    embed_scenario_texts(definition, store, "demo", DEVICE, encoder_factory=FakeEncoder)
    assert definition.model is not None and definition.model.text_challenger is not None
    changed = definition.model_copy(
        update={
            "model": definition.model.model_copy(
                update={"text_challenger": definition.model.text_challenger.model_copy(update={"hf_model_id": "x/y"})}
            )
        }
    )
    FakeEncoder.calls = []
    embed_scenario_texts(changed, store, "demo", DEVICE, encoder_factory=FakeEncoder)
    assert FakeEncoder.calls == [["a", "b"]]
    assert load_cache(store, "demo", "local-embed")["hf_model_id"] == "x/y"  # type: ignore[index]
