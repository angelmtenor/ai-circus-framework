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
from dataclasses import dataclass
from typing import Any

import joblib
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import MODEL_EXPLAINER_KEY, MODEL_METADATA_KEY, MODEL_PIPELINE_KEY
from sklearn.pipeline import Pipeline

from prediction.core.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ModelArtifacts:
    """One tenant's trained pipeline, SHAP explainer, and training metadata for one scenario."""

    pipeline: Pipeline
    explainer: Any
    metadata: dict[str, Any]


class ModelCache:
    """Lazily loads and caches `ModelArtifacts` per (org_id, scenario_slug)."""

    def __init__(self, stores: dict[str, ObjectStore]) -> None:
        """Bind this cache to one ObjectStore per loaded scenario_slug (own MinIO bucket each)."""
        self._stores = stores
        self._cache: dict[tuple[str, str], ModelArtifacts] = {}
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    def _lock_for(self, key: tuple[str, str]) -> threading.Lock:
        """Return the same Lock instance for a given key across concurrent callers."""
        with self._locks_guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
        """Return the tenant's model artifacts for this scenario, loading+caching on first call."""
        key = (org_id, scenario_slug)
        if key in self._cache:
            return self._cache[key]

        with self._lock_for(key):
            if key not in self._cache:  # re-check: another thread may have just populated it
                logger.info("Loading model artifacts for org={} scenario={} from MinIO (cache miss)", *key)
                store = self._stores[scenario_slug]
                pipeline = joblib.load(io.BytesIO(store.get(org_id, MODEL_PIPELINE_KEY)))
                explainer = joblib.load(io.BytesIO(store.get(org_id, MODEL_EXPLAINER_KEY)))
                metadata = json.loads(store.get(org_id, MODEL_METADATA_KEY))
                self._cache[key] = ModelArtifacts(pipeline=pipeline, explainer=explainer, metadata=metadata)
        return self._cache[key]
