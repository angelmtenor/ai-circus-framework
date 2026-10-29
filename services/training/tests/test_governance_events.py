"""Tests for the content-free `model.trained` governance event."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from ai_circus_shared.scenario_schema import ScenarioDefinition

from training.core.governance_events import emit_model_trained

SCENARIOS_DIR = Path(__file__).resolve().parents[3] / "scenarios"
METADATA: dict[str, Any] = {
    "model_name": "lightgbm",
    "task_type": "classification",
    "selection_metric": "roc_auc",
    "test_score": 0.91,
    "training_rows": 800,
    "metrics": {"roc_auc": 0.91, "accuracy": 0.85, "note": "not a number", "nan": float("nan")},
    # Things that must never reach an event:
    "feature_columns": ["Age", "Surname"],
    "global_feature_importance": [{"feature": "Age", "importance": 0.4}],
}


class Config:
    """Just the governance fields emit_model_trained reads."""

    def __init__(self, enabled: str | None) -> None:
        """Set the flag under test."""
        self.GOVERNANCE_EVENTS_ENABLED = enabled
        self.GOVERNANCE_EVENT_SINK = "log"


def _definition(slug: str) -> ScenarioDefinition:
    return ScenarioDefinition.load(SCENARIOS_DIR / slug / "scenario.yaml")


def _events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "ai_circus.governance.events"]


def test_a_finished_run_emits_one_content_free_event(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="ai_circus.governance.events"):
        emit_model_trained(
            Config("true"),
            slug="prestaciones_sociales",
            definition=_definition("prestaciones_sociales"),
            metadata=METADATA,
            org_id="demo",
        )

    (event,) = _events(caplog)
    assert event["type"] == "dev.aicircus.governance.model.trained.v1"
    assert event["source"] == "/training" and event["tenantid"] == "demo"
    assert event["aisystemid"] == "prestaciones_sociales"
    assert event["data"] == {
        "scenario_slug": "prestaciones_sociales",
        "ai_system_id": "prestaciones_sociales",
        "org_id": "demo",
        "model_name": "lightgbm",
        "task_type": "classification",
        "selection_metric": "roc_auc",
        "test_score": 0.91,
        "training_rows": 800,
        "metrics": {"roc_auc": 0.91, "accuracy": 0.85},
    }
    assert "Surname" not in json.dumps(event) and "feature" not in json.dumps(event)


@pytest.mark.parametrize("flag", [None, "false", ""])
def test_off_by_default_emits_nothing(flag: str | None, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="ai_circus.governance.events"):
        emit_model_trained(
            Config(flag), slug="churn", definition=_definition("churn"), metadata=METADATA, org_id="demo"
        )
    assert _events(caplog) == []


def test_a_config_without_governance_fields_is_simply_off(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="ai_circus.governance.events"):
        emit_model_trained(object(), slug="churn", definition=_definition("churn"), metadata=METADATA, org_id="demo")
    assert _events(caplog) == []


def test_a_broken_metadata_dict_never_fails_the_run() -> None:
    emit_model_trained(
        Config("true"),
        slug="churn",
        definition=_definition("churn"),
        metadata={"metrics": 5, "training_rows": "x"},
        org_id="demo",
    )
