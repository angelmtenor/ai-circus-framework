"""Tests for ai_circus_shared.business_rules — the deterministic gate before a model."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from ai_circus_shared.business_rules import (
    BusinessRules,
    DecisionPolicy,
    RuleCondition,
    check,
    decide,
    evaluate,
    fold,
    gate_of,
    is_missing,
    passes,
)


def _rules(*rules: dict) -> BusinessRules:
    return BusinessRules.model_validate(
        {
            "families": [{"key": "docs", "label": "Docs"}, {"key": "req", "label": "Requirements"}],
            "rules": list(rules),
        }
    )


def _rule(key: str, outcome: str = "request_info", family: str = "docs", **condition: object) -> dict:
    return {"key": key, "label": key, "family": family, "outcome": outcome, "message": f"{key} fired", **condition}


@pytest.mark.parametrize(
    ("condition", "record", "holds"),
    [
        ({"field": "a", "op": "missing"}, {"a": None}, True),
        ({"field": "a", "op": "missing"}, {"a": float("nan")}, True),
        ({"field": "a", "op": "missing"}, {"a": "   "}, True),
        ({"field": "a", "op": "missing"}, {}, True),
        ({"field": "a", "op": "missing"}, {"a": 0}, False),
        ({"field": "a", "op": "equals", "value": "No"}, {"a": "No"}, True),
        ({"field": "a", "op": "equals", "value": 3}, {"a": "3.0"}, True),
        ({"field": "a", "op": "not_equals", "value": "No"}, {"a": "Sí"}, True),
        ({"field": "a", "op": "in", "value": ["x", "y"]}, {"a": "y"}, True),
        ({"field": "a", "op": "not_in", "value": ["x", "y"]}, {"a": "y"}, False),
        ({"field": "a", "op": "lt", "value": 6}, {"a": 5}, True),
        ({"field": "a", "op": "lte", "value": 6}, {"a": 6}, True),
        ({"field": "a", "op": "gt", "value": 1500}, {"a": 1500}, False),
        ({"field": "a", "op": "gte", "value": 1500}, {"a": "1500"}, True),
        ({"field": "a", "op": "gte", "value_field": "b"}, {"a": 3, "b": 3}, True),
        ({"field": "a", "op": "gte", "value_field": "b"}, {"a": 2, "b": 3}, False),
        # A comparison never fires on a missing value — that's a `missing` rule's job.
        ({"field": "a", "op": "lt", "value": 6}, {"a": None}, False),
        ({"field": "a", "op": "gte", "value_field": "b"}, {"a": 3, "b": None}, False),
        ({"field": "a", "op": "lt", "value": 6}, {"a": "not a number"}, False),
        (
            {"field": "t", "op": "contains_any", "value": ["violencia de género"]},
            {"t": "Sufro VIOLENCIA de genero"},
            True,
        ),
        ({"field": "t", "op": "contains_any", "value": ["desahucio"]}, {"t": "Pago el alquiler sin problemas"}, False),
    ],
)
def test_check_every_op(condition: dict, record: dict, holds: bool) -> None:
    assert check(RuleCondition.model_validate(condition), record)[0] is holds


def test_contains_any_reports_the_phrases_found() -> None:
    condition = RuleCondition(field="t", op="contains_any", value=["desahucio", "corte de luz", "ictus"])
    assert check(condition, {"t": "Tengo un desahucio y un CORTE DE LUZ"}) == (True, "desahucio, corte de luz")


@pytest.mark.parametrize(
    "condition",
    [
        {"field": "a", "op": "missing", "value": 1},
        {"field": "a", "op": "equals"},
        {"field": "a", "op": "equals", "value": 1, "value_field": "b"},
        {"field": "a", "op": "in", "value": "x"},
        {"field": "a", "op": "in", "value": []},
        {"field": "a", "op": "equals", "value": ["x"]},
        {"field": "a", "op": "lt", "value": "six"},
        {"field": "a", "op": "contains_any", "value": [1, 2]},
    ],
)
def test_condition_rejects_a_malformed_operand(condition: dict) -> None:
    with pytest.raises(ValidationError):
        RuleCondition.model_validate(condition)


def test_rules_reject_duplicated_keys_and_unknown_families() -> None:
    with pytest.raises(ValidationError, match="duplicated"):
        _rules(_rule("R1", field="a", op="missing"), _rule("R1", field="b", op="missing"))
    with pytest.raises(ValidationError, match="unknown families"):
        _rules(_rule("R1", family="nope", field="a", op="missing"))


def test_gate_is_the_most_severe_fired_outcome() -> None:
    rules = _rules(
        _rule("R1", "request_info", field="dni", op="equals", value="No"),
        _rule("R2", "reject", "req", field="months", op="lt", value=6),
        _rule("P1", "review", field="text", op="contains_any", value=["desahucio"]),
    )
    record = {"dni": "No", "months": 3, "text": "un desahucio"}
    outcome = evaluate(rules, record)
    assert outcome.gate == "reject"
    assert [r.key for r in outcome.results if r.fired] == ["R1", "R2", "P1"]
    assert gate_of(rules, {"dni": "Sí", "months": 12, "text": "un desahucio"}) == "review"
    assert gate_of(rules, {"dni": "Sí", "months": 12, "text": "todo bien"}) is None
    assert gate_of(None, record) is None


def test_when_limits_where_a_rule_applies() -> None:
    rules = _rules(
        _rule(
            "R4",
            field="contract",
            op="equals",
            value="No",
            when={"field": "concept", "op": "in", "value": ["rent"]},
        )
    )
    rent = evaluate(rules, {"concept": "rent", "contract": "No"}).results[0]
    food = evaluate(rules, {"concept": "food", "contract": "No"}).results[0]
    assert (rent.applicable, rent.fired, rent.evidence) == (True, True, "No")
    assert (food.applicable, food.fired) == (False, False)


def test_passes_only_blocks_on_reject_and_request_info() -> None:
    rules = _rules(
        _rule("R1", "request_info", field="dni", op="equals", value="No"),
        _rule("P1", "review", field="text", op="contains_any", value=["desahucio"]),
    )
    assert not passes(rules, {"dni": "No", "text": ""})
    assert passes(rules, {"dni": "Sí", "text": "desahucio"})
    assert passes(None, {})


def test_decide_bands_and_gates() -> None:
    policy = DecisionPolicy(approve_at=0.7, deny_at=0.3)
    assert decide(policy, 0.7, None) == "approve"
    assert decide(policy, 0.3, None) == "deny"
    assert decide(policy, 0.5, None) == "review"
    assert decide(policy, 0.99, "review") == "review"
    assert decide(policy, 0.99, "request_info") == "request_info"
    assert decide(policy, 0.01, "reject") == "reject"


def test_policy_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="deny_at"):
        DecisionPolicy(approve_at=0.4, deny_at=0.6)


def test_helpers() -> None:
    assert fold("Género ÑANDÚ") == "genero nandu"
    assert is_missing(math.nan) and is_missing("") and not is_missing("0") and not is_missing(0.0)
