"""Tests for platform_registry.core.seed against the repo's real scenarios/*.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_circus_shared.auth import ADMIN_ORG_ID, ENGINEERING_DEMO_ORG_ID
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_registry.core.models import Base, Entitlement, LlmSetting, Scenario
from platform_registry.core.seed import seed_default_llm_setting, seed_scenarios

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

    assert set(slugs) == {
        "churn",
        "mpm",
        "supply_chain",
        "supermarket_sales",
        "electric_motor",
        "energy_building",
        "ai_circus_reference",
        "service_request",
    }
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


def test_seed_scenarios_populates_task_type_and_target_units(session: Session) -> None:
    """model.task_type/target_units are seeded, distinguishing classification from regression."""
    seed_scenarios(session, SCENARIOS_DIR)

    churn = session.get(Scenario, "churn")
    assert churn.task_type == "classification"
    assert churn.target_units is None

    supply_chain = session.get(Scenario, "supply_chain")
    assert supply_chain.task_type == "regression"
    assert supply_chain.target_units == "days"

    reference = session.get(Scenario, "ai_circus_reference")
    assert reference.task_type is None


def test_seed_scenarios_conversational_rag_has_no_feature_fields(session: Session) -> None:
    """conversational_rag scenarios have no feature_columns/feature_schema, but do get sample_questions."""
    seed_scenarios(session, SCENARIOS_DIR)

    scenario = session.get(Scenario, "ai_circus_reference")
    assert scenario.feature_columns is None
    assert scenario.feature_schema is None
    assert len(scenario.sample_questions) > 0


def test_seed_scenarios_populates_form_config_for_assisted_form_only(session: Session) -> None:
    """assisted_form scenarios get `form`; other kinds don't."""
    seed_scenarios(session, SCENARIOS_DIR)

    service_request = session.get(Scenario, "service_request")
    assert service_request.form["classification_field"] == "request_type"
    field_ids = {f["id"] for f in service_request.form["fields"]}
    assert {"full_name", "email", "address"} <= field_ids

    assert session.get(Scenario, "churn").form is None
    assert session.get(Scenario, "ai_circus_reference").form is None


def test_seed_scenarios_populates_target(session: Session) -> None:
    """tabular_ml scenarios get the predicted column's name; conversational_rag ones don't."""
    seed_scenarios(session, SCENARIOS_DIR)

    assert session.get(Scenario, "churn").target == "Exited"
    assert session.get(Scenario, "supply_chain").target == "ActualShippingDays"
    assert session.get(Scenario, "ai_circus_reference").target is None


def test_seed_scenarios_populates_credits_for_ported_datasets_only(session: Session) -> None:
    """Ported public-dataset scenarios get `credits`; the original ai_circus_reference doesn't."""
    seed_scenarios(session, SCENARIOS_DIR)

    churn = session.get(Scenario, "churn")
    assert churn.credits["source"].startswith("Kaggle")
    assert churn.credits["url"].startswith("https://")

    reference = session.get(Scenario, "ai_circus_reference")
    assert reference.credits is None


def test_seed_scenarios_auto_grants_admin_org_every_scenario(session: Session) -> None:
    """The admin org gets a real, seeded entitlement to every scenario — not a bypass."""
    seed_scenarios(session, SCENARIOS_DIR)

    admin_slugs = {e.scenario_slug for e in session.query(Entitlement).filter_by(org_id=ADMIN_ORG_ID)}
    assert admin_slugs == {
        "churn",
        "mpm",
        "supply_chain",
        "supermarket_sales",
        "electric_motor",
        "energy_building",
        "ai_circus_reference",
        "service_request",
    }


def test_seed_scenarios_auto_grants_engineering_demo_org_only_the_engineering_scenarios(session: Session) -> None:
    """The engineering-demo org gets a real, seeded entitlement to exactly mpm/electric_motor/energy_building."""
    seed_scenarios(session, SCENARIOS_DIR)

    demo_slugs = {e.scenario_slug for e in session.query(Entitlement).filter_by(org_id=ENGINEERING_DEMO_ORG_ID)}
    assert demo_slugs == {"mpm", "electric_motor", "energy_building"}


def test_seed_scenarios_is_idempotent(session: Session) -> None:
    """Re-seeding updates scenarios and admin/engineering-demo entitlements in place, without duplicates."""
    seed_scenarios(session, SCENARIOS_DIR)
    seed_scenarios(session, SCENARIOS_DIR)

    assert session.query(Scenario).count() == 8
    assert session.query(Entitlement).filter_by(org_id=ADMIN_ORG_ID).count() == 8
    assert session.query(Entitlement).filter_by(org_id=ENGINEERING_DEMO_ORG_ID).count() == 3


def test_seed_default_llm_setting_inserts_on_first_boot(session: Session) -> None:
    """A fresh DB gets the default active model seeded."""
    seed_default_llm_setting(session, default_model_name="llama3")

    assert session.get(LlmSetting, 1).model_name == "llama3"


def test_seed_default_llm_setting_never_overwrites_an_existing_choice(session: Session) -> None:
    """Re-running the seed on restart doesn't clobber an admin's already-saved model choice."""
    seed_default_llm_setting(session, default_model_name="llama3")
    session.get(LlmSetting, 1).model_name = "gemini-flash"
    session.commit()

    seed_default_llm_setting(session, default_model_name="llama3")

    assert session.get(LlmSetting, 1).model_name == "gemini-flash"
