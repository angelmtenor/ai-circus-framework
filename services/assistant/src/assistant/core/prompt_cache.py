"""
- Title:    Per-(tenant, scenario) system-prompt cache
- Author:   ai-circus-framework contributors

Mirrors prediction's ModelCache: `assistant` is one long-running service shared by
every tenant of every tabular_ml scenario in SCENARIOS, but each tenant has their own
trained model/metadata in MinIO per scenario — the grounding system prompt is built
lazily per (org_id, scenario_slug) and cached thereafter. A per-key lock avoids a
cold-start cache stampede, same as ModelCache.
"""

from __future__ import annotations

import json
import threading

from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import MODEL_METADATA_KEY

from assistant.core.chat import build_system_prompt
from assistant.core.logger import get_logger

logger = get_logger(__name__)


class SystemPromptCache:
    """Lazily builds and caches the grounding system prompt per (org_id, scenario_slug)."""

    def __init__(self, stores: dict[str, ObjectStore], definitions: dict[str, ScenarioDefinition]) -> None:
        """Bind this cache to one ObjectStore + ScenarioDefinition per loaded scenario_slug."""
        self._stores = stores
        self._definitions = definitions
        self._cache: dict[tuple[str, str], str] = {}
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    def _lock_for(self, key: tuple[str, str]) -> threading.Lock:
        """Return the same Lock instance for a given key across concurrent callers."""
        with self._locks_guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def get(self, org_id: str, scenario_slug: str) -> str:
        """Return the tenant's grounding system prompt for this scenario, building+caching on first call."""
        key = (org_id, scenario_slug)
        if key in self._cache:
            return self._cache[key]

        with self._lock_for(key):
            if key not in self._cache:
                logger.info("Building system prompt for org={} scenario={} (cache miss)", *key)
                metadata = json.loads(self._stores[scenario_slug].get(org_id, MODEL_METADATA_KEY))
                self._cache[key] = build_system_prompt(self._definitions[scenario_slug], metadata)
        return self._cache[key]
