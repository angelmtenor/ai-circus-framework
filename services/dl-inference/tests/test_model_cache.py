"""Tests for core/model_cache.py against real (tiny) ONNX artifacts in a memory store."""

from __future__ import annotations

import json

import pytest
from ai_circus_shared.deep_learning import DL_METADATA_KEY, DL_MODEL_KEY

from dl_inference.core import model_cache as mc
from tests.fixtures import MemoryStore, publish


def _cache(store: MemoryStore) -> mc.DlModelCache:
    return mc.DlModelCache({"triage": store}, fallback_org_id="demo", threads=1)  # type: ignore[dict-item]


def test_loads_the_tenants_own_model_when_it_has_one() -> None:
    store = MemoryStore()
    publish(store, "acme", "text")
    publish(store, "demo", "image")
    model = _cache(store).get("acme", "triage")
    assert model.org_id == "acme"
    assert model.metadata["modality"] == "text"
    assert model.tokenizer is not None
    assert set(model.samples_by_id) == {"s-0", "s-1"}


def test_falls_back_to_the_shared_baseline_org() -> None:
    store = MemoryStore()
    publish(store, "demo", "text")
    assert _cache(store).get("acme", "triage").org_id == "demo"


def test_untrained_scenario_is_a_model_not_trained_error() -> None:
    with pytest.raises(mc.ModelNotTrainedError, match="make dl-train"):
        _cache(MemoryStore()).get("acme", "triage")


def test_tampered_artifact_is_refused() -> None:
    store = MemoryStore()
    publish(store, "demo", "text")
    store.put("demo", DL_MODEL_KEY, b"not the model that was checksummed")
    with pytest.raises(mc.CorruptArtifactError, match="'model'"):
        _cache(store).get("demo", "triage")


def test_cache_hit_skips_the_store_until_revalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    publish(store, "demo", "text")
    cache = _cache(store)
    first = cache.get("demo", "triage")
    reads = len(store.gets)
    assert cache.get("demo", "triage") is first
    assert len(store.gets) == reads  # fresh cache hit: no I/O at all

    monkeypatch.setattr(mc, "REVALIDATE_SECONDS", 0.0)
    assert cache.get("demo", "triage") is first  # unchanged manifest: same object
    assert store.gets[-1].endswith(DL_METADATA_KEY)


def test_retrained_model_is_picked_up_on_revalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    publish(store, "demo", "text")
    cache = _cache(store)
    first = cache.get("demo", "triage")
    monkeypatch.setattr(mc, "REVALIDATE_SECONDS", 0.0)
    publish(store, "demo", "text", trained_at="later")
    second = cache.get("demo", "triage")
    assert second is not first
    assert second.metadata["trained_at"] == "later"


def test_lru_bounds_the_number_of_loaded_models(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    publish(store, "demo", "text")
    monkeypatch.setattr(mc, "MAX_CACHED_MODELS", 2)
    cache = _cache(store)
    for org in ("a", "b", "c"):
        cache.get(org, "triage")
    assert cache.cached_keys() == [("b", "triage"), ("c", "triage")]


def test_images_come_from_the_org_the_model_was_loaded_from_and_are_cached() -> None:
    store = MemoryStore()
    publish(store, "demo", "image")
    cache = _cache(store)
    data = cache.image("acme", "triage", "s-0")
    assert data.startswith(b"\x89PNG")
    reads = len(store.gets)
    assert cache.image("acme", "triage", "s-0") == data
    assert len(store.gets) == reads
    with pytest.raises(ValueError, match="Invalid sample id"):
        cache.image("acme", "triage", "../../etc")


def test_explanation_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    publish(store, "demo", "text")
    model = _cache(store).get("demo", "triage")
    monkeypatch.setattr(mc, "MAX_CACHED_EXPLANATIONS", 2)
    for i in range(3):
        model.remember_explanation(f"s-{i}", 0, {"i": i})
    assert model.cached_explanation("s-0", 0) is None
    assert model.cached_explanation("s-2", 0) == {"i": 2}
    assert json.loads(json.dumps(model.metadata))["modality"] == "text"
