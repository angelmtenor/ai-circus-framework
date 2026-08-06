"""Tests for platform_registry.core.seed against the repo's real scenarios/*.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_circus_shared.auth import ADMIN_ORG_ID
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_registry.core.models import Base, Entitlement, Scenario
from platform_registry.core.seed import seed_scenarios

SCENARIOS_DIR = Path(__file__).resolve().parents[3] / "scenarios"


@pytest.fixture
def session() -> Session:
    """An in-memory SQLite session with the platform-registry schema created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_seed_scenarios_loads_all_repo_scenarios(session: Session) -> None:
    """Every scenarios/*/scenario.yaml in the repo seeds successfully."""
    slugs = seed_scenarios(session, SCENARIOS_DIR)

    assert set(slugs) == {"churn", "docs_rag", "mpm"}
    churn = session.get(Scenario, "churn")
    assert churn.kind == "tabular_ml"
    assert churn.role_required == "scenario:churn"


def test_seed_scenarios_populates_tabular_ml_form_schema_and_sample_questions(session: Session) -> None:
    """tabular_ml scenarios get feature_columns/feature_schema + chat.sample_questions."""
    seed_scenarios(session, SCENARIOS_DIR)

    churn = session.get(Scenario, "churn")
    assert "CreditScore" in churn.feature_columns
    assert churn.feature_schema["CreditScore"]["type"] == "numeric"
    assert len(churn.sample_questions) > 0

    mpm = session.get(Scenario, "mpm")
    assert "Type" in mpm.feature_columns
    assert mpm.feature_schema["Type"]["type"] == "categorical"


def test_seed_scenarios_conversational_rag_has_no_feature_fields(session: Session) -> None:
    """conversational_rag scenarios have no feature_columns/feature_schema, but do get sample_questions."""
    seed_scenarios(session, SCENARIOS_DIR)

    docs_rag = session.get(Scenario, "docs_rag")
    assert docs_rag.feature_columns is None
    assert docs_rag.feature_schema is None
    assert len(docs_rag.sample_questions) > 0


def test_seed_scenarios_auto_grants_admin_org_every_scenario(session: Session) -> None:
    """The admin org gets a real, seeded entitlement to every scenario — not a bypass."""
    seed_scenarios(session, SCENARIOS_DIR)

    admin_slugs = {e.scenario_slug for e in session.query(Entitlement).filter_by(org_id=ADMIN_ORG_ID)}
    assert admin_slugs == {"churn", "docs_rag", "mpm"}


def test_seed_scenarios_is_idempotent(session: Session) -> None:
    """Re-seeding updates scenarios and admin entitlements in place, without duplicates."""
    seed_scenarios(session, SCENARIOS_DIR)
    seed_scenarios(session, SCENARIOS_DIR)

    assert session.query(Scenario).count() == 3
    assert session.query(Entitlement).filter_by(org_id=ADMIN_ORG_ID).count() == 3
