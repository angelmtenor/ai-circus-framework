"""Tests for the chat-over-tabular-data grounding system prompt."""

from __future__ import annotations

from ai_circus_shared.scenario_schema import ChatConfig, ScenarioDefinition, TabularServices

from assistant.core.chat import build_system_prompt

DEFINITION = ScenarioDefinition(
    slug="churn",
    kind="tabular_ml",
    title="Customer Churn Prediction",
    description="  Predicts churn risk from account/usage features.  \n",
    role_required="scenario:churn",
    icon="📉",
    industry="banking_finance",
    chat=ChatConfig(context="A retail bank's customer churn model."),
    services=TabularServices(etl="etl-tabular", training="training", prediction="prediction", assistant="assistant"),
)

METADATA = {
    "model_name": "random_forest",
    "test_score": 0.8605,
    "task_type": "classification",
    "target": "Exited",
    "feature_columns": ["CreditScore", "Geography", "Age"],
}

REGRESSION_METADATA = {
    "model_name": "random_forest",
    "test_score": 0.912,
    "task_type": "regression",
    "target": "ActualShippingDays",
    "feature_columns": ["Carrier", "YShippingDistance"],
}


def test_build_system_prompt_includes_scenario_and_model_details() -> None:
    """The system prompt grounds the assistant in the scenario title/chat.context and model metadata."""
    prompt = build_system_prompt(DEFINITION, METADATA)

    assert "Customer Churn Prediction" in prompt
    assert "A retail bank's customer churn model." in prompt
    assert "random_forest" in prompt
    assert "86.05%" in prompt
    assert "Exited" in prompt
    assert "CreditScore, Geography, Age" in prompt


def test_build_system_prompt_uses_r2_wording_for_regression() -> None:
    """A regression scenario's prompt cites an R² score, not a percentage accuracy."""
    prompt = build_system_prompt(DEFINITION, REGRESSION_METADATA)

    assert "test R² of 0.912" in prompt
    assert "ActualShippingDays" in prompt


def test_build_system_prompt_cites_global_shap_importance_when_present() -> None:
    """When training has computed global_feature_importance, the prompt cites it by name and score."""
    metadata = {**METADATA, "global_feature_importance": [{"feature": "Age", "importance": 0.127}]}

    prompt = build_system_prompt(DEFINITION, metadata)

    assert "Age (0.1270)" in prompt
    assert "global SHAP importance" in prompt


def test_build_system_prompt_omits_importance_sentence_when_absent() -> None:
    """Metadata written before global_feature_importance existed degrades gracefully, not with an error."""
    prompt = build_system_prompt(DEFINITION, METADATA)

    assert "global SHAP importance" not in prompt


def test_build_system_prompt_names_the_prediction_service_tools() -> None:
    """The prompt tells the model to call the real-data tools instead of claiming it lacks data."""
    prompt = build_system_prompt(DEFINITION, METADATA)

    assert "get_dataset_sample" in prompt
    assert "get_predictions_vs_actuals" in prompt
    assert "predict_records" in prompt
