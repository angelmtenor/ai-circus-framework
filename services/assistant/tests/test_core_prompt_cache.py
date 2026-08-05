"""Tests for the per-tenant system-prompt cache."""

from __future__ import annotations

import json

import pytest
from ai_circus_shared.scenario_schema import ScenarioDefinition, TabularServices
from ai_circus_shared.tabular_ml import MODEL_METADATA_KEY

from assistant.core.prompt_cache import SystemPromptCache

DEFINITION = ScenarioDefinition(
    slug="churn",
    kind="tabular_ml",
    title="Customer Churn Prediction",
    description="Predicts churn.",
    role_required="scenario:churn",
    icon="📉",
    services=TabularServices(etl="etl-tabular", training="training", prediction="prediction", assistant="assistant"),
)

METADATA = {
    "model_name": "random_forest",
    "test_accuracy": 0.86,
    "target": "Exited",
    "feature_columns": ["CreditScore"],
}


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore, counting gets."""

    def __init__(self) -> None:
        """Seed metadata for org-1 and track get() calls."""
        self.get_calls: list[tuple[str, str]] = []
        self._objects = {("org-1", MODEL_METADATA_KEY): json.dumps(METADATA).encode()}

    def get(self, org_id: str, path: str) -> bytes:
        """Retrieve previously stored bytes, recording the call for assertions."""
        self.get_calls.append((org_id, path))
        return self._objects[org_id, path]


@pytest.fixture
def store() -> FakeObjectStore:
    """A store pre-seeded with model metadata for org-1."""
    return FakeObjectStore()


def test_get_builds_prompt_on_first_call(store: FakeObjectStore) -> None:
    """A cache miss loads metadata and builds the grounding system prompt."""
    cache = SystemPromptCache(store, DEFINITION)

    prompt = cache.get("org-1")

    assert "Customer Churn Prediction" in prompt
    assert "random_forest" in prompt
    assert len(store.get_calls) == 1


def test_get_caches_across_calls(store: FakeObjectStore) -> None:
    """A second call for the same org doesn't hit the store again."""
    cache = SystemPromptCache(store, DEFINITION)

    cache.get("org-1")
    cache.get("org-1")

    assert len(store.get_calls) == 1
