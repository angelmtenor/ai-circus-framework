"""Tests for the per-(tenant, scenario) model/explainer cache."""

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


def _seed(store: FakeObjectStore, org_id: str, model_name: str) -> None:
    pipeline_buffer = io.BytesIO()
    joblib.dump({"fake": "pipeline"}, pipeline_buffer)
    store.put(org_id, MODEL_PIPELINE_KEY, pipeline_buffer.getvalue())

    explainer_buffer = io.BytesIO()
    joblib.dump({"fake": "explainer"}, explainer_buffer)
    store.put(org_id, MODEL_EXPLAINER_KEY, explainer_buffer.getvalue())

    store.put(org_id, MODEL_METADATA_KEY, json.dumps({"model_name": model_name}).encode())


@pytest.fixture
def stores() -> dict[str, FakeObjectStore]:
    """One fake store per scenario, each pre-seeded with fake artifacts for org-1."""
    churn_store = FakeObjectStore()
    _seed(churn_store, "org-1", "random_forest")
    mpm_store = FakeObjectStore()
    _seed(mpm_store, "org-1", "logistic_regression")
    return {"churn": churn_store, "mpm": mpm_store}


def test_get_loads_artifacts_on_first_call(stores: dict[str, FakeObjectStore]) -> None:
    """A cache miss loads all three artifacts from the scenario's own store."""
    cache = ModelCache(stores)

    artifacts = cache.get("org-1", "churn")

    assert artifacts.pipeline == {"fake": "pipeline"}
    assert artifacts.explainer == {"fake": "explainer"}
    assert artifacts.metadata == {"model_name": "random_forest"}
    assert len(stores["churn"].get_calls) == 3


def test_get_caches_across_calls(stores: dict[str, FakeObjectStore]) -> None:
    """A second call for the same (org, scenario) doesn't hit the store again."""
    cache = ModelCache(stores)

    cache.get("org-1", "churn")
    cache.get("org-1", "churn")

    assert len(stores["churn"].get_calls) == 3


def test_different_scenarios_are_cached_independently(stores: dict[str, FakeObjectStore]) -> None:
    """Same org, different scenario_slug — each loads from its own store, cached separately."""
    cache = ModelCache(stores)

    churn_artifacts = cache.get("org-1", "churn")
    mpm_artifacts = cache.get("org-1", "mpm")

    assert churn_artifacts.metadata["model_name"] == "random_forest"
    assert mpm_artifacts.metadata["model_name"] == "logistic_regression"
    assert len(stores["churn"].get_calls) == 3
    assert len(stores["mpm"].get_calls) == 3
