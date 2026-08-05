"""
- Title:    Per-tenant model/explainer cache
- Author:   ai-circus-framework contributors

`prediction` is one long-running service shared by every tenant of a scenario (unlike
etl-tabular/training, which run once per tenant) — the trained pipeline/explainer are
loaded from MinIO lazily on first request per org_id and cached in memory thereafter.
"""

from __future__ import annotations

import io
import json
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
    """A tenant's trained pipeline, SHAP explainer, and training metadata."""

    pipeline: Pipeline
    explainer: Any
    metadata: dict[str, Any]


class ModelCache:
    """Lazily loads and caches `ModelArtifacts` per tenant (org_id)."""

    def __init__(self, store: ObjectStore) -> None:
        """Bind this cache to an ObjectStore; nothing is loaded until first use."""
        self._store = store
        self._cache: dict[str, ModelArtifacts] = {}

    def get(self, org_id: str) -> ModelArtifacts:
        """Return the tenant's model artifacts, loading and caching them on first call."""
        if org_id not in self._cache:
            logger.info("Loading model artifacts for org={} from MinIO (cache miss)", org_id)
            pipeline = joblib.load(io.BytesIO(self._store.get(org_id, MODEL_PIPELINE_KEY)))
            explainer = joblib.load(io.BytesIO(self._store.get(org_id, MODEL_EXPLAINER_KEY)))
            metadata = json.loads(self._store.get(org_id, MODEL_METADATA_KEY))
            self._cache[org_id] = ModelArtifacts(pipeline=pipeline, explainer=explainer, metadata=metadata)
        return self._cache[org_id]
