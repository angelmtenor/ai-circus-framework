"""Tests for platform_registry.core.seed against the repo's real scenarios/*.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_registry.core.models import Base, Scenario
from platform_registry.core.seed import seed_scenarios

SCENARIOS_DIR = Path(__file__).resolve().parents[3] / "scenarios"


@pytest.fixture
def session() -> Session:
    """An in-memory SQLite session with the platform-registry schema created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_seed_scenarios_loads_both_repo_scenarios(session: Session) -> None:
    """Both scenarios/churn and scenarios/docs_rag seed successfully."""
    slugs = seed_scenarios(session, SCENARIOS_DIR)

    assert set(slugs) == {"churn", "docs_rag"}
    churn = session.get(Scenario, "churn")
    assert churn.kind == "tabular_ml"
    assert churn.role_required == "scenario:churn"


def test_seed_scenarios_is_idempotent(session: Session) -> None:
    """Re-seeding updates in place rather than creating duplicate rows."""
    seed_scenarios(session, SCENARIOS_DIR)
    seed_scenarios(session, SCENARIOS_DIR)

    assert session.query(Scenario).count() == 2
