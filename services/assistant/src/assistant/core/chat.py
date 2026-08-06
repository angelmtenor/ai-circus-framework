"""
- Title:    Chat-over-tabular-data (grounded prompt + llm-gateway call)
- Author:   ai-circus-framework contributors

Ports smart-data-science's "Hybrid Assistant" chat-over-data idea onto llm-gateway,
dropping its regex/`exec()`-based code execution: this just grounds a system prompt
in the scenario's description and the trained model's real metadata, then asks the
question through a normal chat completion. No arbitrary code execution.
"""

from __future__ import annotations

from typing import Any, cast

from ai_circus_shared.scenario_schema import ScenarioDefinition
from openai import OpenAI
from openai.types.chat import ChatCompletion


def build_system_prompt(definition: ScenarioDefinition, metadata: dict[str, Any]) -> str:
    """Ground the assistant in the scenario's chat.context and the trained model's metadata.

    `definition.chat.context` is the human-authored domain framing (see
    scenarios/<slug>/scenario.yaml) — it supplements, not replaces, the real
    feature-list/accuracy grounding already available from the trained model.
    """
    feature_columns = ", ".join(str(c) for c in metadata["feature_columns"])
    if metadata["task_type"] == "regression":
        score_phrase = f"test R² of {float(metadata['test_score']):.3f}"
    else:
        score_phrase = f"test accuracy {float(metadata['test_score']):.2%}"
    return (
        f"You are a data analyst assistant for the '{definition.title}' scenario.\n"
        f"{definition.chat.context.strip()}\n\n"
        f"A {metadata['model_name']} model was trained on this data with {score_phrase}. "
        f"It predicts '{metadata['target']}' from these "
        f"features: {feature_columns}.\n\n"
        "Answer questions about the data, the model, and its predictions clearly and concisely. "
        "If asked something outside this scope, say so plainly rather than guessing."
    )


def chat(
    client: OpenAI,
    model: str,
    system_prompt: str,
    history: list[dict[str, str]],
    message: str,
) -> str:
    """Send the conversation to llm-gateway and return the assistant's reply text."""
    messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": message}]
    response = cast(ChatCompletion, client.chat.completions.create(model=model, messages=messages, stream=False))
    return response.choices[0].message.content or ""
