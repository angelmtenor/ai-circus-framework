"""Tests for the per-(tenant, scenario) system-prompt cache."""

from __future__ import annotations

import json

import pytest
from ai_circus_shared.scenario_schema import ChatConfig, ScenarioDefinition, TabularServices
from ai_circus_shared.tabular_ml import MODEL_METADATA_KEY

from assistant.core.prompt_cache import SystemPromptCache

CHURN_DEFINITION = ScenarioDefinition(
    slug="churn",
    kind="tabular_ml",
    title="Customer Churn Prediction",
    description="Predicts churn.",
    role_required="scenario:churn",
    icon="📉",
    chat=ChatConfig(context="A retail bank's customer churn model."),
    services=TabularServices(etl="etl-tabular", training="training", prediction="prediction", assistant="assistant"),
)

MPM_DEFINITION = ScenarioDefinition(
    slug="mpm",
    kind="tabular_ml",
    title="Machine Predictive Maintenance",
    description="Predicts machine failure.",
    role_required="scenario:mpm",
    icon="⚙️",
    chat=ChatConfig(context="An industrial predictive-maintenance model."),
    services=TabularServices(etl="etl-tabular", training="training", prediction="prediction", assistant="assistant"),
)

CHURN_METADATA = {
    "model_name": "random_forest",
    "test_accuracy": 0.86,
    "target": "Exited",
    "feature_columns": ["CreditScore"],
}

MPM_METADATA = {
    "model_name": "logistic_regression",
    "test_accuracy": 0.97,
    "target": "Target",
    "feature_columns": ["Torque [Nm]"],
}


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore, counting gets."""

    def __init__(self, metadata: dict[str, object]) -> None:
        """Seed metadata for org-1 and track get() calls."""
        self.get_calls: list[tuple[str, str]] = []
        self._objects = {("org-1", MODEL_METADATA_KEY): json.dumps(metadata).encode()}

    def get(self, org_id: str, path: str) -> bytes:
        """Retrieve previously stored bytes, recording the call for assertions."""
        self.get_calls.append((org_id, path))
        return self._objects[org_id, path]


@pytest.fixture
def stores() -> dict[str, FakeObjectStore]:
    """One fake store per scenario, each pre-seeded with model metadata for org-1."""
    return {"churn": FakeObjectStore(CHURN_METADATA), "mpm": FakeObjectStore(MPM_METADATA)}


@pytest.fixture
def definitions() -> dict[str, ScenarioDefinition]:
    """Both scenario definitions, keyed by slug."""
    return {"churn": CHURN_DEFINITION, "mpm": MPM_DEFINITION}


def test_get_builds_prompt_on_first_call(
    stores: dict[str, FakeObjectStore], definitions: dict[str, ScenarioDefinition]
) -> None:
    """A cache miss loads metadata and builds the grounding system prompt."""
    cache = SystemPromptCache(stores, definitions)

    prompt = cache.get("org-1", "churn")

    assert "Customer Churn Prediction" in prompt
    assert "random_forest" in prompt
    assert stores["churn"].get_calls == [("org-1", MODEL_METADATA_KEY)]


def test_get_caches_across_calls(
    stores: dict[str, FakeObjectStore], definitions: dict[str, ScenarioDefinition]
) -> None:
    """A second call for the same (org, scenario) doesn't hit the store again."""
    cache = SystemPromptCache(stores, definitions)

    cache.get("org-1", "churn")
    cache.get("org-1", "churn")

    assert len(stores["churn"].get_calls) == 1


def test_different_scenarios_are_cached_independently(
    stores: dict[str, FakeObjectStore], definitions: dict[str, ScenarioDefinition]
) -> None:
    """Same org, different scenario_slug — each builds from its own store/definition."""
    cache = SystemPromptCache(stores, definitions)

    churn_prompt = cache.get("org-1", "churn")
    mpm_prompt = cache.get("org-1", "mpm")

    assert "Customer Churn Prediction" in churn_prompt
    assert "Machine Predictive Maintenance" in mpm_prompt
    assert len(stores["churn"].get_calls) == 1
    assert len(stores["mpm"].get_calls) == 1
