"""
- Title:    Seed the `scenarios` table from scenarios/*/scenario.yaml
- Author:   ai-circus-framework contributors

`scenarios/*.yaml` is the human-editable source of truth for what a scenario *is*;
this module is the only place that file is read — every other service and both UIs
resolve scenario metadata through this service's API instead of the filesystem.
"""

from __future__ import annotations

from pathlib import Path

from ai_circus_shared.scenario_schema import RagServices, TabularServices, load_all
from sqlalchemy.orm import Session

from platform_registry.core.logger import get_logger
from platform_registry.core.models import Scenario

logger = get_logger(__name__)


def seed_scenarios(session: Session, scenarios_dir: Path) -> list[str]:
    """Upsert every `scenarios/<slug>/scenario.yaml` into the `scenarios` table.

    Args:
        session: An open SQLAlchemy session (caller commits).
        scenarios_dir: Path to the repo's `scenarios/` directory.

    Returns:
        The slugs that were seeded.
    """
    definitions = load_all(scenarios_dir)
    slugs = []

    for definition in definitions:
        existing = session.get(Scenario, definition.slug)
        if existing is None:
            existing = Scenario(slug=definition.slug)
            session.add(existing)

        existing.kind = definition.kind
        existing.title = definition.title
        existing.description = definition.description.strip()
        existing.icon = definition.icon
        existing.role_required = definition.role_required

        if definition.kind == "tabular_ml":
            assert definition.dataset is not None
            assert isinstance(definition.services, TabularServices)
            existing.prediction_service = definition.services.prediction
            existing.assistant_service = definition.services.assistant
            existing.agent_service = None
            existing.feature_columns = definition.dataset.feature_columns
            existing.feature_schema = {k: v.model_dump() for k, v in definition.dataset.feature_schema.items()}
        else:
            assert isinstance(definition.services, RagServices)
            existing.prediction_service = None
            existing.assistant_service = None
            existing.agent_service = definition.services.agent
            existing.feature_columns = None
            existing.feature_schema = None

        slugs.append(definition.slug)

    session.commit()
    logger.info("Seeded {} scenario(s) from {}: {}", len(slugs), scenarios_dir, ", ".join(slugs))
    return slugs
