"""
- Title:    `model.trained` governance event (content-free)
- Author:   Angel Martinez-Tenor

After a model and its metadata are safely stored, a training run can announce itself as a
CloudEvents governance event — scenario, ai_system_id, tenant, chosen model and its holdout
metrics, nothing else (no rows, no feature values, no free text). Off unless
`GOVERNANCE_EVENTS_ENABLED` is true, and — like the MLflow mirror — it can never fail the
job: the emitter swallows and logs every error.
"""

from __future__ import annotations

from typing import Any

from ai_circus_shared.governance import resolve_governance
from ai_circus_shared.governance_events import (
    GovernanceEvents,
    GovernanceEventSettings,
    ModelTrainedData,
    finite_metrics,
)
from ai_circus_shared.scenario_schema import ScenarioDefinition

from training.core.logger import get_logger

logger = get_logger(__name__)


def emit_model_trained(
    config: object, *, slug: str, definition: ScenarioDefinition, metadata: dict[str, Any], org_id: str
) -> None:
    """Announce a stored model. Never raises."""
    try:
        events = GovernanceEvents(GovernanceEventSettings.from_config(config, source="/training"))
        if not events.enabled:
            return
        ai_system_id = str(resolve_governance(slug, definition.governance)["ai_system_id"])
        test_score = metadata.get("test_score")
        events.emit(
            "model.trained",
            ModelTrainedData(
                scenario_slug=slug,
                ai_system_id=ai_system_id,
                org_id=org_id,
                model_name=str(metadata.get("model_name", "unknown")),
                task_type=metadata.get("task_type"),
                selection_metric=metadata.get("selection_metric"),
                test_score=float(test_score) if isinstance(test_score, int | float) else None,
                training_rows=metadata.get("training_rows"),
                metrics=finite_metrics(metadata.get("metrics")),
            ),
            tenant_id=org_id,
            ai_system_id=ai_system_id,
            subject=slug,
        )
    except Exception:  # an evidence side-channel must never fail a finished training run
        logger.exception("model.trained governance event skipped for scenario={}", slug)
