"""
- Title:    Per-tenant system-prompt cache
- Author:   ai-circus-framework contributors

Mirrors prediction's ModelCache: `assistant` is one long-running service shared by
every tenant of a scenario, but each tenant has their own trained model/metadata in
MinIO — the grounding system prompt is built lazily per org_id and cached thereafter.
"""

from __future__ import annotations

import json

from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import MODEL_METADATA_KEY

from assistant.core.chat import build_system_prompt
from assistant.core.logger import get_logger

logger = get_logger(__name__)


class SystemPromptCache:
    """Lazily builds and caches the grounding system prompt per tenant (org_id)."""

    def __init__(self, store: ObjectStore, definition: ScenarioDefinition) -> None:
        """Bind this cache to an ObjectStore and the scenario's definition."""
        self._store = store
        self._definition = definition
        self._cache: dict[str, str] = {}

    def get(self, org_id: str) -> str:
        """Return the tenant's grounding system prompt, building and caching it on first call."""
        if org_id not in self._cache:
            logger.info("Building system prompt for org={} from MinIO (cache miss)", org_id)
            metadata = json.loads(self._store.get(org_id, MODEL_METADATA_KEY))
            self._cache[org_id] = build_system_prompt(self._definition, metadata)
        return self._cache[org_id]
