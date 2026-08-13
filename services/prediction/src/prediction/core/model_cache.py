"""
- Title:    Per-(tenant, scenario) model/explainer cache
- Author:   ai-circus-framework contributors

One `prediction` instance serves every tabular_ml scenario in SCENARIOS, shared by
every tenant of each — the trained pipeline/explainer are loaded from MinIO lazily on
first request per (org_id, scenario_slug) and cached in memory thereafter. A per-key
lock avoids a cold-start cache stampede (N concurrent first-requests for the same key
each redundantly hitting MinIO) without needing to convert this service to async.
"""

from __future__ import annotations

import io
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import joblib
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import (
    MODEL_CHECKSUMS_METADATA_FIELD,
    MODEL_EXPLAINER_KEY,
    MODEL_METADATA_KEY,
    MODEL_PIPELINE_KEY,
    MODEL_PIPELINE_LOWER_KEY,
    MODEL_PIPELINE_UPPER_KEY,
    artifact_checksum,
)
from sklearn.pipeline import Pipeline

from prediction.core.logger import get_logger

logger = get_logger(__name__)


class ModelUnavailableError(RuntimeError):
    """Base for errors meaning a scenario's model artifacts can't be served right now
    (not yet trained, or corrupt) — as opposed to a bug. api.py registers one handler
    for this base class so callers get a clean 503 instead of an unhandled 500 (which
    would skip CORSMiddleware entirely and surface to the browser as an opaque
    "Failed to fetch").
    """


class CorruptArtifactError(ModelUnavailableError):
    """Raised when a MinIO artifact's bytes don't match its metadata checksum —
    i.e. it was partially overwritten by an interrupted retrain, or corrupted/tampered
    with in storage. Refuse to deserialize it rather than joblib.load()'ing unknown
    bytes.
    """


class ModelNotTrainedError(ModelUnavailableError):
    """Raised when no trained model artifacts exist for a scenario, for either the
    tenant's own org or the shared fallback org (e.g. `training` never ran, or errored
    out, for this scenario) — as opposed to `store.get()` bubbling up a raw MinIO 404.
    """


def _load_checked(store: ObjectStore, org_id: str, key: str, checksums: dict[str, str], artifact_name: str) -> Any:
    """Download, checksum-verify, then joblib.load() one model artifact."""
    data = store.get(org_id, key)
    expected = checksums.get(artifact_name)
    if expected is None or artifact_checksum(data) != expected:
        raise CorruptArtifactError(
            f"Checksum mismatch for {artifact_name!r} (org={org_id}, key={key}) — refusing to load it."
        )
    return joblib.load(io.BytesIO(data))


@dataclass(frozen=True)
class ModelArtifacts:
    """One tenant's trained pipeline, SHAP explainer, and training metadata for one scenario.

    `pipeline_lower`/`pipeline_upper` are the 90% prediction-interval models — only
    present for regression scenarios (see training/core/training.py's
    fit_quantile_pipelines()), None otherwise.
    """

    pipeline: Pipeline
    explainer: Any
    metadata: dict[str, Any]
    pipeline_lower: Pipeline | None = None
    pipeline_upper: Pipeline | None = None


#: Each entry holds a full sklearn pipeline + SHAP explainer (can be multi-MB) — bound
#: the cache so an ever-growing set of distinct (org, scenario) tenants can't grow this
#: service's memory without limit. Evicts the least-recently-used entry once full.
MAX_CACHED_TENANTS = 64


class ModelCache:
    """Lazily loads and caches `ModelArtifacts` per (org_id, scenario_slug), bounded to
    `MAX_CACHED_TENANTS` entries (LRU eviction).
    """

    def __init__(self, stores: dict[str, ObjectStore], fallback_org_id: str) -> None:
        """Bind this cache to one ObjectStore per loaded scenario_slug (own MinIO bucket each).

        `fallback_org_id` is the tenant (matching training's ORG_ID) every other
        tenant's model lookup falls back to until it has its own artifacts in MinIO —
        without this, any tenant besides the one training actually ran for (e.g. the
        admin/engineering-demo bypass tenants, or a brand-new Logto organization) would
        404 on every predict call, contradicting this cache's own "shared by every
        tenant" premise.
        """
        self._stores = stores
        self.fallback_org_id = fallback_org_id
        self._cache: OrderedDict[tuple[str, str], ModelArtifacts] = OrderedDict()
        # Guards every read/write of `self._cache` itself (hit-path bump, insert,
        # eviction). Distinct from the per-key locks below, which only serialize the
        # expensive MinIO load+joblib.load work so concurrent loads of *different*
        # keys don't block each other — without this separate guard, a fast-path hit
        # on one key could run unsynchronized against another key's slow-path
        # eviction (`popitem`), raising a spurious KeyError under concurrent load.
        self._cache_guard = threading.Lock()
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    def _lock_for(self, key: tuple[str, str]) -> threading.Lock:
        """Return the same Lock instance for a given key across concurrent callers."""
        with self._locks_guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def store_for(self, scenario_slug: str) -> ObjectStore:
        """Return the raw/normalized-dataset ObjectStore bound to this scenario's bucket."""
        return self._stores[scenario_slug]

    def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
        """Return the tenant's model artifacts for this scenario, loading+caching on first call."""
        key = (org_id, scenario_slug)
        with self._cache_guard:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

        with self._lock_for(key):
            with self._cache_guard:  # re-check: another thread may have just populated it
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]

            store = self._stores[scenario_slug]
            # Tenants without their own trained artifacts yet (any org besides the
            # one training actually ran for) share the baseline org's model —
            # see __init__'s fallback_org_id docstring.
            own_exists = store.exists(org_id, MODEL_METADATA_KEY)
            load_org_id = org_id if own_exists else self.fallback_org_id
            if not own_exists:
                logger.info(
                    "No model artifacts for org={} scenario={} yet — falling back to shared baseline org={}",
                    org_id,
                    scenario_slug,
                    load_org_id,
                )
                if not store.exists(load_org_id, MODEL_METADATA_KEY):
                    raise ModelNotTrainedError(
                        f"No trained model artifacts for scenario={scenario_slug!r} "
                        f"(org={org_id!r}, fallback org={load_org_id!r} also has none — has `training` run for it?)."
                    )
            logger.info("Loading model artifacts for org={} scenario={} from MinIO (cache miss)", load_org_id, scenario_slug)
            # Read metadata first — it's the manifest training writes last, once
            # every artifact below it has been confirmed uploaded — so an
            # interrupted retrain shows up here as a checksum mismatch rather than
            # a silent mix of old/new artifacts.
            metadata = json.loads(store.get(load_org_id, MODEL_METADATA_KEY))
            checksums = metadata.get(MODEL_CHECKSUMS_METADATA_FIELD, {})
            pipeline = _load_checked(store, load_org_id, MODEL_PIPELINE_KEY, checksums, "pipeline")
            explainer = _load_checked(store, load_org_id, MODEL_EXPLAINER_KEY, checksums, "explainer")
            pipeline_lower = pipeline_upper = None
            if metadata.get("has_intervals") and "pipeline_lower" in checksums and "pipeline_upper" in checksums:
                pipeline_lower = _load_checked(store, load_org_id, MODEL_PIPELINE_LOWER_KEY, checksums, "pipeline_lower")
                pipeline_upper = _load_checked(store, load_org_id, MODEL_PIPELINE_UPPER_KEY, checksums, "pipeline_upper")
            artifacts = ModelArtifacts(
                pipeline=pipeline,
                explainer=explainer,
                metadata=metadata,
                pipeline_lower=pipeline_lower,
                pipeline_upper=pipeline_upper,
            )
            with self._cache_guard:
                if key not in self._cache:
                    if len(self._cache) >= MAX_CACHED_TENANTS:
                        evicted_key, _ = self._cache.popitem(last=False)
                        logger.info("Model cache full — evicted org={} scenario={}", *evicted_key)
                        with self._locks_guard:
                            self._locks.pop(evicted_key, None)
                    self._cache[key] = artifacts
                return self._cache[key]
