"""Tests for the chat-over-tabular-data prompt building and completion call."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ai_circus_shared.scenario_schema import ChatConfig, ScenarioDefinition, TabularServices

from assistant.core.chat import build_system_prompt, chat

DEFINITION = ScenarioDefinition(
    slug="churn",
    kind="tabular_ml",
    title="Customer Churn Prediction",
    description="  Predicts churn risk from account/usage features.  \n",
    role_required="scenario:churn",
    icon="📉",
    chat=ChatConfig(context="A retail bank's customer churn model."),
    services=TabularServices(etl="etl-tabular", training="training", prediction="prediction", assistant="assistant"),
)

METADATA = {
    "model_name": "random_forest",
    "test_accuracy": 0.8605,
    "target": "Exited",
    "feature_columns": ["CreditScore", "Geography", "Age"],
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


def test_chat_sends_system_history_and_message_and_returns_reply() -> None:
    """chat() assembles the full message list and returns the completion's reply text."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="The top feature is Age."))]
    )
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    reply = chat(fake_client, "gpt-4o-mini", "system prompt", history, "what matters most?")

    assert reply == "The top feature is Age."
    _, kwargs = fake_client.chat.completions.create.call_args
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["messages"] == [
        {"role": "system", "content": "system prompt"},
        *history,
        {"role": "user", "content": "what matters most?"},
    ]


def test_chat_returns_empty_string_for_none_content() -> None:
    """A completion with no content (e.g. a refusal) returns an empty string, not None."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
    )

    reply = chat(fake_client, "gpt-4o-mini", "system prompt", [], "hi")

    assert reply == ""
