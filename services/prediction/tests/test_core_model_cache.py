"""Tests for the per-(tenant, scenario) model/explainer cache."""

from __future__ import annotations

import io
import json

import joblib
import pytest
from ai_circus_shared.tabular_ml import (
    MODEL_CHECKSUMS_METADATA_FIELD,
    MODEL_EXPLAINER_KEY,
    MODEL_METADATA_KEY,
    MODEL_PIPELINE_KEY,
    artifact_checksum,
)

from prediction.core.model_cache import MAX_CACHED_TENANTS, CorruptArtifactError, ModelCache


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

    def exists(self, org_id: str, path: str) -> bool:
        """Mirror ObjectStore.exists() against the in-memory object map."""
        return (org_id, path) in self.objects


def _seed(store: FakeObjectStore, org_id: str, model_name: str) -> None:
    pipeline_buffer = io.BytesIO()
    joblib.dump({"fake": "pipeline"}, pipeline_buffer)
    pipeline_bytes = pipeline_buffer.getvalue()
    store.put(org_id, MODEL_PIPELINE_KEY, pipeline_bytes)

    explainer_buffer = io.BytesIO()
    joblib.dump({"fake": "explainer"}, explainer_buffer)
    explainer_bytes = explainer_buffer.getvalue()
    store.put(org_id, MODEL_EXPLAINER_KEY, explainer_bytes)

    metadata = {
        "model_name": model_name,
        MODEL_CHECKSUMS_METADATA_FIELD: {
            "pipeline": artifact_checksum(pipeline_bytes),
            "explainer": artifact_checksum(explainer_bytes),
        },
    }
    store.put(org_id, MODEL_METADATA_KEY, json.dumps(metadata).encode())


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
    cache = ModelCache(stores, fallback_org_id="fallback-org")

    artifacts = cache.get("org-1", "churn")

    assert artifacts.pipeline == {"fake": "pipeline"}
    assert artifacts.explainer == {"fake": "explainer"}
    assert artifacts.metadata["model_name"] == "random_forest"
    assert len(stores["churn"].get_calls) == 3


def test_get_caches_across_calls(stores: dict[str, FakeObjectStore]) -> None:
    """A second call for the same (org, scenario) doesn't hit the store again."""
    cache = ModelCache(stores, fallback_org_id="fallback-org")

    cache.get("org-1", "churn")
    cache.get("org-1", "churn")

    assert len(stores["churn"].get_calls) == 3


def test_different_scenarios_are_cached_independently(stores: dict[str, FakeObjectStore]) -> None:
    """Same org, different scenario_slug — each loads from its own store, cached separately."""
    cache = ModelCache(stores, fallback_org_id="fallback-org")

    churn_artifacts = cache.get("org-1", "churn")
    mpm_artifacts = cache.get("org-1", "mpm")

    assert churn_artifacts.metadata["model_name"] == "random_forest"
    assert mpm_artifacts.metadata["model_name"] == "logistic_regression"
    assert len(stores["churn"].get_calls) == 3
    assert len(stores["mpm"].get_calls) == 3


def test_get_evicts_least_recently_used_entry_once_full(stores: dict[str, FakeObjectStore]) -> None:
    """Once MAX_CACHED_TENANTS distinct (org, scenario) pairs are cached, adding one
    more evicts the least-recently-used one instead of growing unbounded.
    """
    churn_store = stores["churn"]
    org_ids = [f"org-{i}" for i in range(MAX_CACHED_TENANTS + 1)]
    for org_id in org_ids:
        _seed(churn_store, org_id, "random_forest")
    cache = ModelCache(stores, fallback_org_id="fallback-org")

    for org_id in org_ids[:-1]:  # fill to exactly MAX_CACHED_TENANTS
        cache.get(org_id, "churn")
    calls_before_overflow = len(churn_store.get_calls)

    cache.get(org_ids[-1], "churn")  # one more than capacity — evicts org_ids[0]

    assert len(cache._cache) == MAX_CACHED_TENANTS
    assert org_ids[0] not in {org for org, _ in cache._cache}
    assert len(churn_store.get_calls) == calls_before_overflow + 3  # the new entry's real load

    cache.get(org_ids[0], "churn")  # evicted — must hit the store again, not just re-cache
    assert len(churn_store.get_calls) == calls_before_overflow + 6


def test_get_moves_entry_to_most_recently_used_on_cache_hit(stores: dict[str, FakeObjectStore]) -> None:
    """Re-fetching an already-cached entry marks it as recently used, so it survives
    eviction even though it was the first one loaded.
    """
    churn_store = stores["churn"]
    org_ids = [f"org-{i}" for i in range(MAX_CACHED_TENANTS + 1)]
    for org_id in org_ids:
        _seed(churn_store, org_id, "random_forest")
    cache = ModelCache(stores, fallback_org_id="fallback-org")

    for org_id in org_ids[:-1]:
        cache.get(org_id, "churn")
    cache.get(org_ids[0], "churn")  # touch the oldest entry again — now most-recent

    cache.get(org_ids[-1], "churn")  # triggers eviction of the *new* least-recently-used

    assert org_ids[0] in {org for org, _ in cache._cache}
    assert org_ids[1] not in {org for org, _ in cache._cache}


def test_get_rejects_pipeline_that_does_not_match_its_checksum(stores: dict[str, FakeObjectStore]) -> None:
    """An interrupted retrain that overwrote the pipeline but not metadata is caught,
    not silently served as a mismatched (old metadata, new pipeline) pair.
    """
    store = stores["churn"]
    tampered = io.BytesIO()
    joblib.dump({"fake": "a different pipeline"}, tampered)
    store.put("org-1", MODEL_PIPELINE_KEY, tampered.getvalue())

    with pytest.raises(CorruptArtifactError):
        ModelCache(stores, fallback_org_id="fallback-org").get("org-1", "churn")


def test_get_falls_back_to_shared_baseline_org_when_tenant_has_no_artifacts(
    stores: dict[str, FakeObjectStore],
) -> None:
    """A tenant with no trained model of its own (e.g. the admin/engineering-demo bypass
    orgs, or a brand-new Logto organization) gets the fallback org's artifacts instead
    of a KeyError — this is what makes the cache actually "shared by every tenant."
    """
    cache = ModelCache(stores, fallback_org_id="org-1")

    artifacts = cache.get("new-tenant-with-no-model", "churn")

    assert artifacts.metadata["model_name"] == "random_forest"


def test_get_prefers_the_tenants_own_artifacts_over_the_fallback(stores: dict[str, FakeObjectStore]) -> None:
    """Once a tenant has its own trained artifacts, those are used instead of the fallback."""
    _seed(stores["churn"], "org-2", "gradient_boosting")
    cache = ModelCache(stores, fallback_org_id="org-1")

    artifacts = cache.get("org-2", "churn")

    assert artifacts.metadata["model_name"] == "gradient_boosting"


def test_get_caches_the_fallback_result_under_the_requesting_org(stores: dict[str, FakeObjectStore]) -> None:
    """A fallback load is still cached per-requester — a second call for the same
    org-with-no-model doesn't re-hit the store.
    """
    cache = ModelCache(stores, fallback_org_id="org-1")

    cache.get("new-tenant-with-no-model", "churn")
    calls_after_first = len(stores["churn"].get_calls)
    cache.get("new-tenant-with-no-model", "churn")

    assert len(stores["churn"].get_calls) == calls_after_first


def test_get_skips_intervals_when_only_one_quantile_checksum_is_present(stores: dict[str, FakeObjectStore]) -> None:
    """has_intervals=True but only pipeline_lower's checksum was recorded (the upper
    write was interrupted) — treated as no intervals, not a crash on a missing key.
    """
    store = stores["churn"]
    metadata = json.loads(store.get("org-1", MODEL_METADATA_KEY))
    metadata["has_intervals"] = True
    metadata[MODEL_CHECKSUMS_METADATA_FIELD]["pipeline_lower"] = "irrelevant-because-never-loaded"
    store.put("org-1", MODEL_METADATA_KEY, json.dumps(metadata).encode())

    artifacts = ModelCache(stores, fallback_org_id="fallback-org").get("org-1", "churn")

    assert artifacts.pipeline_lower is None
    assert artifacts.pipeline_upper is None
