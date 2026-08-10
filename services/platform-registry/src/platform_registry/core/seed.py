"""
- Title:    Seed the `scenarios` table from scenarios/*/scenario.yaml
- Author:   ai-circus-framework contributors

`scenarios/*.yaml` is the human-editable source of truth for what a scenario *is*;
this module is the only place that file is read — every other service and both UIs
resolve scenario metadata through this service's API instead of the filesystem.
"""

from __future__ import annotations

from pathlib import Path

from ai_circus_shared.auth import ADMIN_ORG_ID
from ai_circus_shared.scenario_schema import load_all
from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_registry.core.logger import get_logger
from platform_registry.core.models import Entitlement, LlmSetting, Scenario

logger = get_logger(__name__)


def seed_scenarios(session: Session, scenarios_dir: Path) -> list[str]:
    """Upsert every `scenarios/<slug>/scenario.yaml` into the `scenarios` table.

    Also auto-grants `ADMIN_ORG_ID` an entitlement to every scenario seeded here —
    not a bypass of entitlement checking (see `ai_circus_shared.auth`), just a real,
    self-maintaining entitlement row so the admin credential always has access to
    every scenario, including ones added after this platform first launched.

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
        existing.sample_questions = definition.chat.sample_questions
        existing.credits = definition.credits.model_dump() if definition.credits is not None else None

        if definition.dataset is not None:
            existing.feature_columns = definition.dataset.feature_columns
            existing.feature_schema = {k: v.model_dump() for k, v in definition.dataset.feature_schema.items()}
            existing.default_charts = [c.model_dump() for c in definition.dataset.default_charts]
            existing.target = definition.dataset.target
        else:
            existing.feature_columns = None
            existing.feature_schema = None
            existing.default_charts = None
            existing.target = None

        if definition.model is not None:
            existing.task_type = definition.model.task_type
            existing.target_units = definition.model.target_units
        else:
            existing.task_type = None
            existing.target_units = None

        admin_stmt = select(Entitlement).where(
            Entitlement.org_id == ADMIN_ORG_ID, Entitlement.scenario_slug == definition.slug
        )
        if session.scalars(admin_stmt).first() is None:
            session.add(Entitlement(org_id=ADMIN_ORG_ID, scenario_slug=definition.slug))

        slugs.append(definition.slug)

    session.commit()
    logger.info("Seeded {} scenario(s) from {}: {}", len(slugs), scenarios_dir, ", ".join(slugs))
    return slugs


def seed_default_llm_setting(session: Session, default_model_name: str) -> None:
    """Insert the singleton `llm_settings` row on first boot only — never overwrites an
    admin's already-saved choice on a later restart.
    """
    if session.get(LlmSetting, 1) is None:
        session.add(LlmSetting(id=1, model_name=default_model_name))
        session.commit()
        logger.info("Seeded default active LLM model: {}", default_model_name)
