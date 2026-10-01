"""Tests for platform_registry.core.seed against the repo's real scenarios/*.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_circus_shared.auth import ADMIN_ORG_ID, ENGINEERING_DEMO_ORG_ID
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_registry.core.models import Base, Entitlement, LlmSetting, Scenario, VoiceSetting
from platform_registry.core.seed import seed_default_llm_setting, seed_default_voice_setting, seed_scenarios

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
        "cnc_surface_finish",
        "steel_defects",
        "turbofan_rul",
        "luznova_regional_demand",
        "luznova_gas_anomaly",
        "luznova_ev_charging",
        "luznova_gas_prospects",
        "ai_circus_reference",
        "service_request",
        "symptom_triage",
        "chest_xray_pneumonia",
        "pcb_visual_inspection",
        "screw_visual_inspection",
        "solar_cell_inspection",
        "titanic",
        "toxic_leadership",
        "bank_early_warning",
        "enron_fraud_network",
        "aml_money_trail",
        "aml_regulation_kg",
        "global_health_shipments",
        "sede_electronica",
        "prestaciones_sociales",
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


def test_seed_scenarios_populates_ui_extras_for_opted_in_scenarios_only(session: Session) -> None:
    """`ui_extras` (the opt-in 5th workspace tab) is seeded only for scenarios that set it."""
    seed_scenarios(session, SCENARIOS_DIR)

    region_map = session.get(Scenario, "luznova_regional_demand")
    assert region_map.ui_extras["kind"] == "region_map"
    levels = {level["key"]: level for level in region_map.ui_extras["levels"]}
    assert levels["region"]["group_by"] is None
    assert len(levels["region"]["regions"]) == 17
    assert levels["province"]["group_by"] == "province"
    assert len(levels["province"]["regions"]) == 50

    live_plant = session.get(Scenario, "mpm")
    assert live_plant.ui_extras["kind"] == "live_plant"
    assert live_plant.ui_extras["machine_count"] == 6
    assert live_plant.ui_extras["wear_feature"] == "Tool wear [min]"
    assert live_plant.ui_extras["sim_minutes_per_tick"] == 5

    optimizer = session.get(Scenario, "cnc_surface_finish")
    assert optimizer.ui_extras["kind"] == "process_optimizer"
    assert optimizer.ui_extras["controllable"] == ["feed_rate", "cutting_speed", "depth_of_cut", "tool_nose_radius"]
    assert optimizer.ui_extras["wear_feature"] == "tool_wear_time"
    assert optimizer.ui_extras["spec_default"] == pytest.approx(3.2)
    assert optimizer.ui_extras["economics"]["tool_life"]["feature"] == "cutting_speed"

    assert session.get(Scenario, "churn").ui_extras is None


def test_seed_scenarios_populates_deep_learning_scenarios(session: Session) -> None:
    """deep_learning scenarios get their `deep_learning` block, plus the generic target
    columns (task type, target label, class labels) the picker/ScenarioView reuse.
    """
    seed_scenarios(session, SCENARIOS_DIR)

    triage = session.get(Scenario, "symptom_triage")
    assert triage.kind == "deep_learning"
    assert triage.industry == "healthcare"
    assert triage.deep_learning["modality"] == "text"
    assert triage.deep_learning["base_model"] == "thomas-sounack/BioClinical-ModernBERT-base"
    assert triage.task_type == "text_classification"
    assert len(triage.target_value_labels) == 22
    assert triage.target_value_labels["gastroesophageal reflux disease"] == "Acid reflux (GERD)"
    assert triage.ui_extras["kind"] == "triage_board"
    assert triage.feature_columns is None

    xray = session.get(Scenario, "chest_xray_pneumonia")
    assert xray.deep_learning["modality"] == "image"
    assert xray.task_type == "image_classification"
    assert xray.target_value_labels == {"0": "Normal", "1": "Pneumonia"}
    assert xray.ui_extras["positive_label"] == "1"

    pcb = session.get(Scenario, "pcb_visual_inspection")
    assert pcb.industry == "manufacturing_industry"
    assert pcb.deep_learning["task"] == "anomaly_detection"
    assert pcb.task_type == "image_anomaly_detection"
    assert pcb.target_value_labels == {"0": "Good", "1": "Defective"}
    assert pcb.ui_extras["kind"] == "triage_board"

    screw = session.get(Scenario, "screw_visual_inspection")
    assert screw.industry == "manufacturing_industry"
    assert screw.task_type == "image_anomaly_detection"
    assert screw.deep_learning["source"]["type"] == "huggingface_image_folder"
    assert screw.ui_extras["item_noun"] == "screw"

    solar = session.get(Scenario, "solar_cell_inspection")
    assert solar.industry == "manufacturing_industry"
    assert solar.deep_learning["task"] == "classification"
    assert solar.target_value_labels == {"0": "Functional", "1": "Defective"}
    assert solar.ui_extras["tab_label"] == "Grading Line"

    assert session.get(Scenario, "churn").deep_learning is None


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
        "cnc_surface_finish",
        "steel_defects",
        "turbofan_rul",
        "luznova_regional_demand",
        "luznova_gas_anomaly",
        "luznova_ev_charging",
        "luznova_gas_prospects",
        "ai_circus_reference",
        "service_request",
        "symptom_triage",
        "chest_xray_pneumonia",
        "pcb_visual_inspection",
        "screw_visual_inspection",
        "solar_cell_inspection",
        "titanic",
        "toxic_leadership",
        "bank_early_warning",
        "enron_fraud_network",
        "aml_money_trail",
        "aml_regulation_kg",
        "global_health_shipments",
        "sede_electronica",
        "prestaciones_sociales",
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

    assert session.query(Scenario).count() == 29
    assert session.query(Entitlement).filter_by(org_id=ADMIN_ORG_ID).count() == 29
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


def test_seed_default_voice_setting_inserts_on_first_boot(session: Session) -> None:
    """A fresh DB gets the default (self-hosted/open) STT/TTS providers seeded."""
    seed_default_voice_setting(session, default_stt_provider="whisper", default_tts_provider="piper")

    setting = session.get(VoiceSetting, 1)
    assert setting.stt_provider == "whisper"
    assert setting.tts_provider == "piper"


def test_seed_default_voice_setting_never_overwrites_an_existing_choice(session: Session) -> None:
    """Re-running the seed on restart doesn't clobber an admin's already-saved provider choice."""
    seed_default_voice_setting(session, default_stt_provider="whisper", default_tts_provider="piper")
    session.get(VoiceSetting, 1).tts_provider = "elevenlabs"
    session.commit()

    seed_default_voice_setting(session, default_stt_provider="whisper", default_tts_provider="piper")

    assert session.get(VoiceSetting, 1).tts_provider == "elevenlabs"


def test_seed_scenarios_populates_the_titanic_tutorial(session: Session) -> None:
    """The tutorial domain's scenario seeds its Voyage extra and its Tutorial block;
    every other scenario has no tutorial.
    """
    seed_scenarios(session, SCENARIOS_DIR)

    titanic = session.get(Scenario, "titanic")
    assert titanic.industry == "tutorial"
    assert titanic.ui_extras["kind"] == "voyage_explorer"
    assert [zone["key"] for zone in titanic.ui_extras["zones"]] == ["1st", "2nd", "3rd"]
    assert titanic.tutorial["tab_label"] == "Tutorial"
    assert "roc_curve" in {w for step in titanic.tutorial["steps"] for w in step["widgets"]}
    assert session.get(Scenario, "churn").tutorial is None


def test_seed_scenarios_populates_the_rubric_check_and_text_challenger(session: Session) -> None:
    """toxic_leadership ships its LLM rubric and transformer challenger to the UI; others don't."""
    seed_scenarios(session, SCENARIOS_DIR)

    toxic = session.get(Scenario, "toxic_leadership")
    assert toxic.feature_schema["Review"]["type"] == "text"
    assert toxic.ui_extras["scene"] == "office_tower"
    assert toxic.rubric_check["text_feature"] == "Review"
    assert {b["key"] for b in toxic.rubric_check["negative"]} >= {"dishonesty", "offloading", "no_depth"}
    assert toxic.text_challenger["embedding_model"] == "local-embed"
    titanic = session.get(Scenario, "titanic")
    assert titanic.rubric_check is None and titanic.text_challenger is None


def test_seed_scenarios_stores_the_official_form_layout_and_the_watchlist(session: Session) -> None:
    """The multi-model form (sections, variants, boxes) and the risk_watchlist extra
    reach the DB intact — ui-react renders both straight from these JSON columns.
    """
    seed_scenarios(session, SCENARIOS_DIR)

    sede = session.get(Scenario, "sede_electronica")
    assert sede is not None and sede.form is not None
    assert sede.form["locale"] == "es"
    assert [v["code"] for v in sede.form["variants"]][:2] == ["Modelo SG-01", "Modelo DC-30"]
    assert sede.form["fields"][1]["casilla"] == "01"
    bank = session.get(Scenario, "bank_early_warning")
    assert bank is not None and bank.ui_extras is not None
    assert bank.ui_extras["kind"] == "risk_watchlist"
    assert bank.industry == "public_sector"


def test_seed_scenarios_stores_the_network_explorer_as_plain_json(session: Session) -> None:
    """The network_explorer extra (tiers, pillars, dated events) reaches the DB as JSON —
    event dates stay strings — and the scenario stays admin-only (not a demo tenant's).
    """
    seed_scenarios(session, SCENARIOS_DIR)

    enron = session.get(Scenario, "enron_fraud_network")
    assert enron is not None and enron.ui_extras is not None
    assert enron.ui_extras["kind"] == "network_explorer"
    assert enron.industry == "public_sector"
    assert all(isinstance(event["date"], str) for event in enron.ui_extras["events"])
    assert enron.ui_extras["flag_from_tier"] == "Review"
    assert (
        session
        .query(Entitlement)
        .filter_by(org_id=ENGINEERING_DEMO_ORG_ID, scenario_slug="enron_fraud_network")
        .count()
        == 0
    )


def test_seed_scenarios_stores_the_money_trail_as_plain_json(session: Session) -> None:
    """The money_trail extra (flows, countries, tiers, pillars) reaches the DB as JSON and
    the scenario stays admin-only (not a demo tenant's).
    """
    seed_scenarios(session, SCENARIOS_DIR)

    aml = session.get(Scenario, "aml_money_trail")
    assert aml is not None and aml.ui_extras is not None
    assert aml.ui_extras["kind"] == "money_trail"
    assert aml.industry == "banking_finance"
    assert {flow["kind"] for flow in aml.ui_extras["flows"]} >= {"ach", "wire", "cheque"}
    assert aml.ui_extras["flag_from_tier"] == "Review"
    assert (
        session.query(Entitlement).filter_by(org_id=ENGINEERING_DEMO_ORG_ID, scenario_slug="aml_money_trail").count()
        == 0
    )


def test_seed_scenarios_populates_business_rules_and_the_decision_policy(session: Session) -> None:
    seed_scenarios(session, SCENARIOS_DIR)

    aid = session.get(Scenario, "prestaciones_sociales")
    assert aid.decision_policy["approve_at"] == pytest.approx(0.7)
    assert aid.decision_policy["deny_at"] == pytest.approx(0.3)
    assert {rule["key"] for rule in aid.business_rules["rules"]} >= {"C1", "V1", "R1", "P1"}
    assert aid.rule_columns["AportaDNI"]["type"] == "categorical"
    assert aid.ui_extras["kind"] == "case_desk"
    assert session.get(Scenario, "titanic").business_rules is None
