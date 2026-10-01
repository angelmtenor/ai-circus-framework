"""Declarative business rules and a decision policy for a `tabular_ml` scenario.

A scenario may gate every record through deterministic rules *before* its model is
applied — a missing document, a value out of range, a legal requirement not met — and
turn the model's probability into one of three proposals (approve / review / deny)
with two thresholds. Both are declared in `scenario.yaml` (`dataset.business_rules`,
`model.decision_policy`) and evaluated here, server-side only: etl-tabular keeps
rule-failing rows out of `dropna()`, training never fits on rows the rules already
decide, and prediction returns the fired rules, the gate and the decision next to the
probability. Pure Python on plain dict records — no pandas, so every service can use it.

A rule *fires* when its condition holds — the condition describes the problem
(`AportaDNI equals "No"`), not the requirement. A comparison against a missing value
never fires: a missing value is the business of a separate `missing` rule.
"""

from __future__ import annotations

import math
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, model_validator

RuleOp = Literal["missing", "equals", "not_equals", "in", "not_in", "lt", "lte", "gt", "gte", "contains_any"]
# What a fired rule does to the record, most severe first:
# reject = the application cannot proceed (a requirement is not met);
# request_info = something is missing or wrong and the applicant must fix it;
# review = the model may still score it, but a person must decide.
RuleOutcome = Literal["reject", "request_info", "review"]
Decision = Literal["approve", "deny", "review", "request_info", "reject"]

GATE_PRECEDENCE: tuple[RuleOutcome, ...] = ("reject", "request_info", "review")
# Gates that stop a record before the model: training never fits on such rows and the
# decision is the gate itself, whatever the probability.
BLOCKING_OUTCOMES: frozenset[str] = frozenset({"reject", "request_info"})

_COMPARISONS = {"lt", "lte", "gt", "gte"}
_MEMBERSHIP = {"in", "not_in", "contains_any"}


class RuleCondition(BaseModel):
    """`<field> <op> <value>` — or `<field> <op> <value_field>` to compare two fields
    of the same record (coherence checks, e.g. minors >= household members)."""

    field: str
    op: RuleOp
    value: str | float | list[str] | list[float] | None = None
    value_field: str | None = None

    @model_validator(mode="after")
    def _operand_matches_op(self) -> RuleCondition:
        if self.op == "missing":
            if self.value is not None or self.value_field is not None:
                raise ValueError(f"rule on {self.field!r}: op 'missing' takes no value.")
            return self
        if (self.value is None) == (self.value_field is None):
            raise ValueError(f"rule on {self.field!r}: op {self.op!r} needs exactly one of value / value_field.")
        if self.op in _MEMBERSHIP:
            if self.value_field is not None or not isinstance(self.value, list) or not self.value:
                raise ValueError(f"rule on {self.field!r}: op {self.op!r} needs a non-empty list value.")
        elif isinstance(self.value, list):
            raise ValueError(f"rule on {self.field!r}: op {self.op!r} takes a single value, not a list.")
        if self.op in _COMPARISONS and isinstance(self.value, str):
            raise ValueError(f"rule on {self.field!r}: op {self.op!r} needs a numeric value.")
        if self.op == "contains_any" and not all(isinstance(v, str) for v in self.value or []):
            raise ValueError(f"rule on {self.field!r}: op 'contains_any' needs a list of phrases.")
        return self

    def fields(self) -> list[str]:
        """Every record field the condition reads."""
        return [self.field, *([self.value_field] if self.value_field else [])]


class BusinessRule(RuleCondition):
    """One rule: fires when its condition holds (and `when`, if set, also holds)."""

    key: str
    label: str  # short name of the check, e.g. "Documento de identidad"
    family: str  # a BusinessRules.families key
    outcome: RuleOutcome
    message: str  # what the applicant/caseworker is told when it fires
    legal_basis: str | None = None
    # Applicability: the rule is only checked for records where this condition holds
    # (e.g. a rent contract is only required when the aid is for rent).
    when: RuleCondition | None = None

    def fields(self) -> list[str]:
        return [*super().fields(), *(self.when.fields() if self.when else [])]


class RuleFamily(BaseModel):
    """A group of rules shown together (e.g. completeness, validity, eligibility)."""

    key: str
    label: str
    description: str | None = None


class BusinessRules(BaseModel):
    """`dataset.business_rules` — the families (display order) and their rules."""

    families: list[RuleFamily] = Field(min_length=1)
    rules: list[BusinessRule] = Field(min_length=1)
    # Display names of each outcome (e.g. {"request_info": "Subsanación"}).
    outcome_labels: dict[RuleOutcome, str] = {
        "reject": "Rejected",
        "request_info": "More information needed",
        "review": "Manual review",
    }

    @model_validator(mode="after")
    def _keys_unique_and_families_known(self) -> BusinessRules:
        keys = [rule.key for rule in self.rules]
        duplicated = sorted({k for k in keys if keys.count(k) > 1})
        if duplicated:
            raise ValueError(f"business_rules: duplicated rule keys {duplicated}.")
        families = {family.key for family in self.families}
        unknown = sorted({rule.family for rule in self.rules} - families)
        if unknown:
            raise ValueError(f"business_rules: rules name unknown families {unknown}.")
        return self


class DecisionPolicy(BaseModel):
    """`model.decision_policy` — turns P(positive class), the *favourable* outcome
    (e.g. "aid granted"), into a proposal: approve at or above `approve_at`, deny at or
    below `deny_at`, anything in between goes to a person (selective prediction)."""

    approve_at: float = Field(gt=0, lt=1)
    deny_at: float = Field(gt=0, lt=1)
    labels: dict[Decision, str] = {
        "approve": "Approve",
        "deny": "Deny",
        "review": "Manual review",
        "request_info": "More information needed",
        "reject": "Rejected",
    }

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> DecisionPolicy:
        if self.deny_at >= self.approve_at:
            raise ValueError("decision_policy: deny_at must be below approve_at.")
        return self


class RuleResult(BaseModel):
    """One rule checked against one record."""

    key: str
    label: str
    family: str
    outcome: RuleOutcome
    applicable: bool  # False = its `when` did not hold, so it was not checked
    fired: bool
    message: str
    legal_basis: str | None = None
    # For a fired `contains_any`: the phrases found; for others: the offending value.
    evidence: str | None = None


class RulesOutcome(BaseModel):
    """Every rule's result, and the gate: the most severe fired outcome (None = pass)."""

    gate: RuleOutcome | None
    results: list[RuleResult]


def fold(text: str) -> str:
    """Casefolded, accent-free text — `contains_any` matches "Violencia de Género"
    against "violencia de genero"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def is_missing(value: object) -> bool:
    """None, NaN, or blank text."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def _as_number(value: object) -> float | None:
    if is_missing(value) or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _same(left: object, right: object) -> bool:
    """Equality that treats 3 and 3.0 and "3" alike — CSV and JSON disagree on types."""
    left_number, right_number = _as_number(left), _as_number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return str(left).strip() == str(right).strip()


def check(condition: RuleCondition, record: dict[str, object]) -> tuple[bool, str | None]:
    """(holds, evidence) for one condition on one record."""
    value = record.get(condition.field)
    if condition.op == "missing":
        return is_missing(value), None
    if is_missing(value):
        return False, None
    operand: object = record.get(condition.value_field) if condition.value_field else condition.value
    if condition.value_field is not None and is_missing(operand):
        return False, None
    evidence = str(value)
    match condition.op:
        case "equals":
            return _same(value, operand), evidence
        case "not_equals":
            return not _same(value, operand), evidence
        case "in":
            return any(_same(value, option) for option in operand), evidence  # type: ignore[union-attr]
        case "not_in":
            return not any(_same(value, option) for option in operand), evidence  # type: ignore[union-attr]
        case "contains_any":
            text = fold(str(value))
            found = [phrase for phrase in operand if fold(str(phrase)) in text]  # type: ignore[union-attr]
            return bool(found), ", ".join(found) or None
        case _:
            left, right = _as_number(value), _as_number(operand)
            if left is None or right is None:
                return False, None
            holds = {
                "lt": left < right,
                "lte": left <= right,
                "gt": left > right,
                "gte": left >= right,
            }[condition.op]
            return holds, evidence


def evaluate(rules: BusinessRules, record: dict[str, object]) -> RulesOutcome:
    """Check every rule against `record`; the gate is the most severe fired outcome."""
    results = []
    for rule in rules.rules:
        applicable = rule.when is None or check(rule.when, record)[0]
        fired, evidence = check(rule, record) if applicable else (False, None)
        results.append(
            RuleResult(
                key=rule.key,
                label=rule.label,
                family=rule.family,
                outcome=rule.outcome,
                applicable=applicable,
                fired=fired,
                message=rule.message,
                legal_basis=rule.legal_basis,
                evidence=evidence if fired else None,
            )
        )
    fired_outcomes = {r.outcome for r in results if r.fired}
    gate = next((outcome for outcome in GATE_PRECEDENCE if outcome in fired_outcomes), None)
    return RulesOutcome(gate=gate, results=results)


def gate_of(rules: BusinessRules | None, record: dict[str, object]) -> RuleOutcome | None:
    """Just the gate (None without rules)."""
    return evaluate(rules, record).gate if rules is not None else None


def passes(rules: BusinessRules | None, record: dict[str, object]) -> bool:
    """True when no blocking rule fires — the record may reach (and train) the model."""
    return gate_of(rules, record) not in BLOCKING_OUTCOMES


def decide(policy: DecisionPolicy, probability: float, gate: RuleOutcome | None) -> Decision:
    """The proposal for one record: a blocking gate wins, a `review` gate forces a
    person whatever the score, otherwise the probability falls in one of three bands."""
    if gate is not None:
        return gate
    if probability >= policy.approve_at:
        return "approve"
    if probability <= policy.deny_at:
        return "deny"
    return "review"
