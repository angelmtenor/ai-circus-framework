"""Tests for the chat-over-tabular-data grounding system prompt."""

from __future__ import annotations

from ai_circus_shared.scenario_schema import ChatConfig, ScenarioDefinition, TabularServices

from assistant.core.chat import build_system_prompt, document_tool_instructions, documents_only_instructions

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

# The same scenario with a document search (`documents.tool`) — the shape of
# prestaciones_sociales' regulations tool.
DEFINITION_WITH_DOCUMENTS = ScenarioDefinition.model_validate({
    **DEFINITION.model_dump(),
    "documents": {
        "bucket": "scenario-churn",
        "raw_prefix": "normativa/",
        "seed_prefix": "sample_docs",
        "chunking": {"strategy": "recursive_character", "chunk_size": 900, "chunk_overlap": 120},
        "embedding": {"model": "voyageai/voyage-4-nano"},
        "tool": {
            "name": "consultar_normativa",
            "label": "Normativa",
            "description": "Busca en la normativa aplicable a las ayudas: requisitos, plazos y procedimiento.",
            "sample_questions": ["¿Cuáles son los requisitos?"],
            "max_calls_per_run": 2,
        },
    },
    "vector_store": {"backend": "qdrant", "collection_prefix": "normativa_demo", "top_k": 4},
})

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


def test_build_system_prompt_cites_cross_validated_and_holdout_roc_auc_when_present() -> None:
    """A model card's ROC AUC figures are quoted so the assistant never has to guess them."""
    metadata = {
        **METADATA,
        "cv_folds": 5,
        "metrics": {"cv_roc_auc_mean": 0.8848, "cv_roc_auc_std": 0.0154, "holdout_roc_auc": 0.8727},
    }
    prompt = build_system_prompt(DEFINITION, metadata)
    assert "5-fold cross-validated ROC AUC 0.885 (± 0.015)" in prompt
    assert "hold-out ROC AUC 0.873" in prompt


def test_build_system_prompt_cites_red_and_green_flag_terms_for_text_features() -> None:
    """A text feature's strongest terms (training's text_term_importance) are cited."""
    metadata = {
        **METADATA,
        "text_term_importance": {
            "Review": {
                "positive": [{"term": "upper management", "weight": 0.31, "docs": 90}],
                "negative": [{"term": "supportive", "weight": -0.2, "docs": 50}],
            }
        },
    }
    prompt = build_system_prompt(DEFINITION, metadata)

    assert "'Review' is free text" in prompt
    assert "'upper management' (+0.310)" in prompt
    assert "'supportive' (-0.200)" in prompt
    assert "free text" not in build_system_prompt(DEFINITION, METADATA)


def test_build_system_prompt_adds_the_document_tool_only_when_the_scenario_has_one() -> None:
    """A scenario with documents.tool is told when to search, how to cite and to tag each source."""
    prompt = build_system_prompt(DEFINITION_WITH_DOCUMENTS, METADATA)

    assert "consultar_normativa" in prompt
    assert "**[Normativa]**" in prompt and "**[Modelo]**" in prompt
    assert "<retrieved_document>" in prompt  # the indirect-prompt-injection guard
    assert document_tool_instructions(DEFINITION) == ""
    assert "consultar_normativa" not in build_system_prompt(DEFINITION, METADATA)


def test_documents_only_instructions_switch_off_the_data_and_model() -> None:
    text = documents_only_instructions(DEFINITION_WITH_DOCUMENTS)

    assert "DOCUMENTS-ONLY MODE" in text and "consultar_normativa" in text
