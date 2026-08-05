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


def test_seed_scenarios_loads_all_repo_scenarios(session: Session) -> None:
    """Every scenarios/*/scenario.yaml in the repo seeds successfully."""
    slugs = seed_scenarios(session, SCENARIOS_DIR)

    assert set(slugs) == {"churn", "docs_rag", "mpm"}
    churn = session.get(Scenario, "churn")
    assert churn.kind == "tabular_ml"
    assert churn.role_required == "scenario:churn"


def test_seed_scenarios_populates_tabular_ml_service_routing_and_form_schema(session: Session) -> None:
    """tabular_ml scenarios get prediction/assistant service names + form schema; no agent_service."""
    seed_scenarios(session, SCENARIOS_DIR)

    churn = session.get(Scenario, "churn")
    assert churn.prediction_service == "prediction"
    assert churn.assistant_service == "assistant"
    assert churn.agent_service is None
    assert "CreditScore" in churn.feature_columns
    assert churn.feature_schema["CreditScore"]["type"] == "numeric"

    mpm = session.get(Scenario, "mpm")
    assert mpm.prediction_service == "prediction-mpm"
    assert mpm.assistant_service == "assistant-mpm"
    assert "Type" in mpm.feature_columns
    assert mpm.feature_schema["Type"]["type"] == "categorical"


def test_seed_scenarios_populates_conversational_rag_agent_service(session: Session) -> None:
    """conversational_rag scenarios get an agent_service; no prediction/assistant/feature fields."""
    seed_scenarios(session, SCENARIOS_DIR)

    docs_rag = session.get(Scenario, "docs_rag")
    assert docs_rag.agent_service == "rag-agent"
    assert docs_rag.prediction_service is None
    assert docs_rag.assistant_service is None
    assert docs_rag.feature_columns is None
    assert docs_rag.feature_schema is None


def test_seed_scenarios_is_idempotent(session: Session) -> None:
    """Re-seeding updates in place rather than creating duplicate rows."""
    seed_scenarios(session, SCENARIOS_DIR)
    seed_scenarios(session, SCENARIOS_DIR)

    assert session.query(Scenario).count() == 3
