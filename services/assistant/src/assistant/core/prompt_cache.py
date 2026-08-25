"""
- Title:    Per-(tenant, scenario) system-prompt cache
- Author:   ai-circus-framework contributors

Mirrors prediction's ModelCache: `assistant` is one long-running service shared by
every tenant of every tabular_ml scenario in SCENARIOS, but each tenant has their own
trained model/metadata in SeaweedFS per scenario — the grounding system prompt is built
lazily per (org_id, scenario_slug) and cached thereafter. A per-key lock avoids a
cold-start cache stampede, same as ModelCache.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict

from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import MODEL_METADATA_KEY

from assistant.core.chat import build_system_prompt
from assistant.core.logger import get_logger

logger = get_logger(__name__)

#: Each entry is a short string, but the set of distinct (org, scenario) tenants a
#: long-running instance sees is unbounded over its lifetime — bound the cache the
#: same way prediction's ModelCache does, evicting the least-recently-used entry.
MAX_CACHED_TENANTS = 256


class SystemPromptCache:
    """Lazily builds and caches the grounding system prompt per (org_id, scenario_slug),
    bounded to `MAX_CACHED_TENANTS` entries (LRU eviction).
    """

    def __init__(
        self,
        stores: dict[str, ObjectStore],
        definitions: dict[str, ScenarioDefinition],
        fallback_org_id: str,
    ) -> None:
        """Bind this cache to one ObjectStore + ScenarioDefinition per loaded scenario_slug.

        `fallback_org_id` mirrors prediction's ModelCache: the tenant (matching
        training's ORG_ID) every other tenant's metadata lookup falls back to until it
        has its own trained model in SeaweedFS.
        """
        self._stores = stores
        self._definitions = definitions
        self._fallback_org_id = fallback_org_id
        self._cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        # Guards every read/write of `self._cache` itself — see prediction's
        # ModelCache for why this must be separate from the per-key load locks below.
        self._cache_guard = threading.Lock()
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
        with self._cache_guard:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

        with self._lock_for(key):
            with self._cache_guard:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]

            store = self._stores[scenario_slug]
            # Tenants without their own trained model yet share the baseline org's
            # metadata — see __init__'s fallback_org_id docstring.
            load_org_id = org_id if store.exists(org_id, MODEL_METADATA_KEY) else self._fallback_org_id
            if load_org_id != org_id:
                logger.info(
                    "No model metadata for org={} scenario={} yet — falling back to shared baseline org={}",
                    org_id,
                    scenario_slug,
                    load_org_id,
                )
            logger.info("Building system prompt for org={} scenario={} (cache miss)", org_id, scenario_slug)
            metadata = json.loads(store.get(load_org_id, MODEL_METADATA_KEY))
            prompt = build_system_prompt(self._definitions[scenario_slug], metadata)

            with self._cache_guard:
                if key not in self._cache:
                    if len(self._cache) >= MAX_CACHED_TENANTS:
                        evicted_key, _ = self._cache.popitem(last=False)
                        logger.info("Prompt cache full — evicted org={} scenario={}", *evicted_key)
                        with self._locks_guard:
                            self._locks.pop(evicted_key, None)
                    self._cache[key] = prompt
                return self._cache[key]
