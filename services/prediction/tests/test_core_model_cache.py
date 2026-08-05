"""Tests for the per-tenant model/explainer cache."""

from __future__ import annotations

import io
import json

import joblib
import pytest
from ai_circus_shared.tabular_ml import MODEL_EXPLAINER_KEY, MODEL_METADATA_KEY, MODEL_PIPELINE_KEY

from prediction.core.model_cache import ModelCache


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore, counting gets."""

    def __init__(self) -> None:
        """Start with an empty object map and a get() call counter."""
        self.objects: dict[tuple[str, str], bytes] = {}
        self.get_calls: list[tuple[str, str]] = []

    def put(self, org_id: str, path: str, data: bytes) -> None:
        """Store bytes under a tenant-scoped path."""
        self.objects[org_id, path] = data

    def get(self, org_id: str, path: str) -> bytes:
        """Retrieve previously stored bytes, recording the call for assertions."""
        self.get_calls.append((org_id, path))
        return self.objects[org_id, path]


@pytest.fixture
def store() -> FakeObjectStore:
    """A store pre-seeded with fake pipeline/explainer/metadata artifacts for one org."""
    fake_store = FakeObjectStore()
    pipeline_buffer = io.BytesIO()
    joblib.dump({"fake": "pipeline"}, pipeline_buffer)
    fake_store.put("org-1", MODEL_PIPELINE_KEY, pipeline_buffer.getvalue())

    explainer_buffer = io.BytesIO()
    joblib.dump({"fake": "explainer"}, explainer_buffer)
    fake_store.put("org-1", MODEL_EXPLAINER_KEY, explainer_buffer.getvalue())

    fake_store.put("org-1", MODEL_METADATA_KEY, json.dumps({"model_name": "random_forest"}).encode())
    return fake_store


def test_get_loads_artifacts_on_first_call(store: FakeObjectStore) -> None:
    """A cache miss loads all three artifacts from the store."""
    cache = ModelCache(store)

    artifacts = cache.get("org-1")

    assert artifacts.pipeline == {"fake": "pipeline"}
    assert artifacts.explainer == {"fake": "explainer"}
    assert artifacts.metadata == {"model_name": "random_forest"}
    assert len(store.get_calls) == 3


def test_get_caches_across_calls(store: FakeObjectStore) -> None:
    """A second call for the same org doesn't hit the store again."""
    cache = ModelCache(store)

    cache.get("org-1")
    cache.get("org-1")

    assert len(store.get_calls) == 3
