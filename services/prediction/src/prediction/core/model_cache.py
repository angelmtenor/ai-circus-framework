"""
- Title:    Per-(tenant, scenario) model/explainer cache
- Author:   Angel Martinez-Tenor

One `prediction` instance serves every tabular_ml scenario in SCENARIOS, shared by
every tenant of each — the trained pipeline/explainer are loaded from SeaweedFS once per
(source org, scenario_slug) and cached in memory thereafter. app.py `preload`s the shared
fallback org's model of every scenario at start-up (all 13 take ~3 s and ~130 MB), so no
demo's first prediction waits for a download; a tenant with its own model loads it lazily. A per-key
lock avoids a cold-start cache stampede (N concurrent first-requests for the same key
each redundantly hitting SeaweedFS) without needing to convert this service to async.
"""

from __future__ import annotations

import io
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import joblib
import pandas as pd
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
    """Raised when a SeaweedFS artifact's bytes don't match its metadata checksum —
    i.e. it was partially overwritten by an interrupted retrain, or corrupted/tampered
    with in storage. Refuse to deserialize it rather than joblib.load()'ing unknown
    bytes.
    """


class ModelNotTrainedError(ModelUnavailableError):
    """Raised when no trained model artifacts exist for a scenario, for either the
    tenant's own org or the shared fallback org (e.g. `training` never ran, or errored
    out, for this scenario) — as opposed to `store.get()` bubbling up a raw SeaweedFS 404.
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
#: Requester -> source-org memo entries kept before the memo is simply reset.
_MAX_REMEMBERED_SOURCES = 4096
#: Normalized datasets are the largest objects here (up to MAX_DATASET_ROWS rows each).
MAX_CACHED_DATASETS = 8
#: Long enough to cover one Data-tab visit's burst of calls; short enough that a re-run
#: of etl-tabular shows up without restarting this service.
DATASET_TTL_SECONDS = 300.0


class ModelCache:
    """Lazily loads and caches `ModelArtifacts` per (org_id, scenario_slug), bounded to
    `MAX_CACHED_TENANTS` entries (LRU eviction).
    """

    def __init__(self, stores: dict[str, ObjectStore], fallback_org_id: str) -> None:
        """Bind this cache to one ObjectStore per loaded scenario_slug (own SeaweedFS bucket each).

        `fallback_org_id` is the tenant (matching training's ORG_ID) every other
        tenant's model lookup falls back to until it has its own artifacts in SeaweedFS —
        without this, any tenant besides the one training actually ran for (e.g. the
        admin/engineering-demo bypass tenants, or a brand-new Keycloak organization) would
        404 on every predict call, contradicting this cache's own "shared by every
        tenant" premise.
        """
        self._stores = stores
        self.fallback_org_id = fallback_org_id
        self._cache: OrderedDict[tuple[str, str], ModelArtifacts] = OrderedDict()
        # Guards every read/write of `self._cache` itself (hit-path bump, insert,
        # eviction). Distinct from the per-key locks below, which only serialize the
        # expensive SeaweedFS load+joblib.load work so concurrent loads of *different*
        # keys don't block each other — without this separate guard, a fast-path hit
        # on one key could run unsynchronized against another key's slow-path
        # eviction (`popitem`), raising a spurious KeyError under concurrent load.
        self._cache_guard = threading.Lock()
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        # (requesting org, scenario) -> org whose artifacts it is served; see _source_org.
        self._sources: dict[tuple[str, str], str] = {}
        self._datasets: dict[tuple[str, str], tuple[pd.DataFrame, float]] = {}

    def _lock_for(self, key: tuple[str, str]) -> threading.Lock:
        """Return the same Lock instance for a given key across concurrent callers."""
        with self._locks_guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def store_for(self, scenario_slug: str) -> ObjectStore:
        """Return the raw/normalized-dataset ObjectStore bound to this scenario's bucket."""
        return self._stores[scenario_slug]

    def _source_org(self, org_id: str, scenario_slug: str) -> str:
        """The org whose artifacts `org_id` is served: its own once trained, else the
        shared fallback (see __init__). Remembered per requester, so the cache-hit path
        costs no SeaweedFS round-trip.
        """
        requester = (org_id, scenario_slug)
        with self._cache_guard:
            source = self._sources.get(requester)
        if source is not None:
            return source
        store = self._stores[scenario_slug]
        if store.exists(org_id, MODEL_METADATA_KEY):
            source = org_id
        else:
            logger.info(
                "No model artifacts for org={} scenario={} yet — falling back to shared baseline org={}",
                org_id,
                scenario_slug,
                self.fallback_org_id,
            )
            if not store.exists(self.fallback_org_id, MODEL_METADATA_KEY):
                raise ModelNotTrainedError(
                    f"No trained model artifacts for scenario={scenario_slug!r} (org={org_id!r}, "
                    f"fallback org={self.fallback_org_id!r} also has none — has `training` run for it?)."
                )
            source = self.fallback_org_id
        with self._cache_guard:
            if len(self._sources) >= _MAX_REMEMBERED_SOURCES:
                self._sources.clear()
            self._sources[requester] = source
        return source

    def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
        """The tenant's model artifacts for this scenario, loading+caching on first call.

        Cached by the org the artifacts actually come from, not the requester: every
        tenant still on the shared fallback model (admin, engineering-demo, each new
        Keycloak org) shares one in-memory copy instead of loading its own.
        """
        key = (self._source_org(org_id, scenario_slug), scenario_slug)
        with self._cache_guard:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

        with self._lock_for(key):
            with self._cache_guard:  # re-check: another thread may have just populated it
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]
            artifacts = self._load(*key)
            with self._cache_guard:
                if key not in self._cache:
                    if len(self._cache) >= MAX_CACHED_TENANTS:
                        evicted_key, _ = self._cache.popitem(last=False)
                        logger.info("Model cache full — evicted org={} scenario={}", *evicted_key)
                        with self._locks_guard:
                            self._locks.pop(evicted_key, None)
                    self._cache[key] = artifacts
                return self._cache[key]

    def preload(self, scenario_slugs: list[str]) -> int:
        """Load the shared fallback org's model of every scenario now (at start-up), so the
        first real request finds it warm. Best-effort per scenario: one not trained yet (a
        fresh cluster before `make k3s-pipeline`) or unreadable is logged and skipped — it
        loads lazily on its first request instead, exactly as before.

        Returns:
            How many scenarios' models are now cached.
        """
        started = time.perf_counter()
        loaded = 0
        for slug in scenario_slugs:
            try:
                self.get(self.fallback_org_id, slug)
            except ModelUnavailableError as exc:
                logger.info("Not pre-loading scenario={}: {}", slug, exc)
                continue
            except Exception:
                logger.exception("Pre-loading scenario={} failed — its first request will retry", slug)
                continue
            loaded += 1
        logger.success(
            "Pre-loaded {}/{} scenario models in {:.1f}s", loaded, len(scenario_slugs), time.perf_counter() - started
        )
        return loaded

    def _load(self, load_org_id: str, scenario_slug: str) -> ModelArtifacts:
        store = self._stores[scenario_slug]
        logger.info(
            "Loading model artifacts for org={} scenario={} from SeaweedFS (cache miss)", load_org_id, scenario_slug
        )
        # Read metadata first — it's the manifest training writes last, once every
        # artifact below it has been confirmed uploaded — so an interrupted retrain
        # shows up here as a checksum mismatch rather than a silent mix of old/new.
        metadata = json.loads(store.get(load_org_id, MODEL_METADATA_KEY))
        checksums = metadata.get(MODEL_CHECKSUMS_METADATA_FIELD, {})
        pipeline = _load_checked(store, load_org_id, MODEL_PIPELINE_KEY, checksums, "pipeline")
        explainer = _load_checked(store, load_org_id, MODEL_EXPLAINER_KEY, checksums, "explainer")
        pipeline_lower = pipeline_upper = None
        if metadata.get("has_intervals") and "pipeline_lower" in checksums and "pipeline_upper" in checksums:
            pipeline_lower = _load_checked(store, load_org_id, MODEL_PIPELINE_LOWER_KEY, checksums, "pipeline_lower")
            pipeline_upper = _load_checked(store, load_org_id, MODEL_PIPELINE_UPPER_KEY, checksums, "pipeline_upper")
        return ModelArtifacts(
            pipeline=pipeline,
            explainer=explainer,
            metadata=metadata,
            pipeline_lower=pipeline_lower,
            pipeline_upper=pipeline_upper,
        )

    def dataset(self, org_id: str, scenario_slug: str) -> pd.DataFrame:
        """The tenant's normalized dataset (own copy, else the fallback org's — see
        `dataset.load_normalized`), cached for `DATASET_TTL_SECONDS`: the Data tab calls
        sample, evaluation and explainability back to back, each of which previously
        re-downloaded and re-parsed the same parquet from SeaweedFS. Treat the returned
        frame as read-only — it is shared between requests.
        """
        requester = (org_id, scenario_slug)
        now = time.monotonic()
        with self._cache_guard:
            cached = self._datasets.get(requester)
            if cached is not None and cached[1] > now:
                return cached[0]
        from prediction.core.dataset import load_normalized  # dataset.py imports this module

        df = load_normalized(self._stores[scenario_slug], org_id, self.fallback_org_id)
        with self._cache_guard:
            if len(self._datasets) >= MAX_CACHED_DATASETS:
                self._datasets.pop(min(self._datasets, key=lambda k: self._datasets[k][1]))
            self._datasets[requester] = (df, now + DATASET_TTL_SECONDS)
        return df
