"""Tests for the LLM rubric check (scenario.yaml `rubric_check`)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from ai_circus_shared.auth import Identity
from ai_circus_shared.scenario_schema import ScenarioDefinition
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistant.api import _chat_llm, _llm_model, _scenario_definition, router
from assistant.core.identity import resolve_identity
from assistant.core.rubric import build_rubric_prompt, parse_rubric_response

REPO = Path(__file__).resolve().parents[3]
TOXIC = ScenarioDefinition.load(REPO / "scenarios/toxic_leadership/scenario.yaml")
RUBRIC = TOXIC.rubric_check
assert RUBRIC is not None
DESCRIPTION = "He presents our work as his own and blames the developers when deadlines slip."


def _answer(**overrides: Any) -> str:
    answer = {
        "verdict": "toxic",
        "balance": -80,
        "summary": "The leader takes credit and shifts blame.",
        "behaviours": [
            {"key": "self_promotion", "evidence": "presents our work as his own", "strength": "strong", "note": "credit"},
            {"key": "blame", "evidence": "blames the developers", "strength": "clear"},
            {"key": "made_up", "evidence": "x"},
            {"key": "coach", "evidence": "mentors everyone weekly", "strength": "huge"},
        ],
        "advice": ["Keep a record of who did what.", "Raise it at a skip-level."],
    }
    return json.dumps({**answer, **overrides})


def test_prompt_contains_the_rubric_rules_and_guardrails() -> None:
    prompt = build_rubric_prompt(RUBRIC)
    assert "`credential_inflation`" not in prompt  # only real keys
    assert "`dishonesty` — Dishonesty & credential inflation" in prompt
    assert "`technical` — Has the technical skills to advise the team" in prompt
    assert "untrusted data" in prompt and "no names" in prompt
    assert RUBRIC.guidance.strip() in prompt


def test_parser_trusts_the_rubric_not_the_model() -> None:
    result = parse_rubric_response(f"Sure! ```json\n{_answer()}\n```", RUBRIC, DESCRIPTION)

    assert result["verdict"] == "toxic" and result["verdict_label"] == "Toxic management"
    keys = [b["key"] for b in result["behaviours"]]
    assert keys == ["self_promotion", "blame", "coach"]  # unknown key dropped
    by_key = {b["key"]: b for b in result["behaviours"]}
    assert by_key["blame"]["polarity"] == "negative" and by_key["coach"]["polarity"] == "positive"
    assert by_key["self_promotion"]["verified"] is True
    assert by_key["coach"]["verified"] is False  # quote not in the description
    assert by_key["coach"]["strength"] == "clear"  # invalid strength normalised
    assert result["advice"] == ["Keep a record of who did what.", "Raise it at a skip-level."]


def test_parser_clamps_and_defaults_and_rejects_non_json() -> None:
    result = parse_rubric_response(_answer(verdict="evil", balance=-900), RUBRIC, DESCRIPTION)
    assert result["verdict"] == "unclear" and result["balance"] == -100
    with pytest.raises(ValueError, match="valid JSON"):
        parse_rubric_response("I cannot help with that.", RUBRIC, DESCRIPTION)


class FakeLLM:
    """Stand-in ChatOpenAI: records the per-request copy and returns a canned answer."""

    def __init__(self, content: str) -> None:
        self.content, self.model_kwargs, self.extra_body, self.updates = content, {}, None, None
        self.messages: list[Any] = []

    def model_copy(self, update: dict[str, Any]) -> FakeLLM:
        self.updates = update
        return self

    def invoke(self, messages: list[Any]) -> SimpleNamespace:
        self.messages = messages
        return SimpleNamespace(content=self.content)


def _client(llm: FakeLLM, definition: ScenarioDefinition = TOXIC) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="org-1", roles=frozenset())
    app.dependency_overrides[_scenario_definition] = lambda: definition
    app.dependency_overrides[_chat_llm] = lambda: llm
    app.dependency_overrides[_llm_model] = lambda: "groq-llama"
    return TestClient(app)


def test_endpoint_returns_the_structured_reading() -> None:
    llm = FakeLLM(_answer())
    body = _client(llm).post("/rubric-check/toxic_leadership", json={"text": DESCRIPTION}).json()

    assert body["verdict"] == "toxic" and body["model"] == "groq-llama"
    assert body["behaviours"][0]["name"] == "Self-promotion & credit-taking"
    assert llm.updates["temperature"] == 0 and llm.updates["model_kwargs"]["user"] == "org-1"
    assert llm.updates["extra_body"]["metadata"]["trace_name"] == "assistant-rubric/toxic_leadership"
    assert DESCRIPTION in llm.messages[1].content


def test_endpoint_guards() -> None:
    assert _client(FakeLLM("not json")).post(
        "/rubric-check/toxic_leadership", json={"text": DESCRIPTION}
    ).status_code == 502
    too_long = "x" * (RUBRIC.max_chars + 1)
    assert _client(FakeLLM(_answer())).post("/rubric-check/toxic_leadership", json={"text": too_long}).status_code == 422
    churn = ScenarioDefinition.load(REPO / "scenarios/churn/scenario.yaml")
    assert _client(FakeLLM(_answer()), churn).post("/rubric-check/churn", json={"text": DESCRIPTION}).status_code == 404
