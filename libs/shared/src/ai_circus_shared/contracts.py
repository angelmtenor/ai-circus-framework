"""
- Title:    Data contracts — JSON Schema validation plus scenario-declared record rules
- Author:   Angel Martinez-Tenor

A `data_product` source points at a JSON Schema file (its contract, see
ai_circus_shared.data_product.KafkaSource) and may add record-level rules JSON Schema can't
express (lines add up to the total, the store exists in the ERP…). `Contract.check` runs both
and returns every violation, so data-ingest can quarantine a record with *all* its reasons in
one go — never just the first.

Needs the `data` extra (`ai-circus-shared[data]`, i.e. `jsonschema`); only the data-tier
services install it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ai_circus_shared.data_product import ContractRule, KafkaSource

MISSING = object()


def get_path(payload: Any, path: str) -> list[Any]:
    """Every value at a dotted payload path; `[*]` fans out over an array.

    "store_id" -> [value]; "lines[*].qty" -> [qty of each line]. A missing key yields an
    empty list (not an error) so callers decide whether absence is a violation.
    """
    values: list[Any] = [payload]
    for part in path.split("."):
        fan_out = part.endswith("[*]")
        key = part.removesuffix("[*]")
        nxt: list[Any] = []
        for value in values:
            if not isinstance(value, dict) or key not in value:
                continue
            child = value[key]
            if fan_out:
                if isinstance(child, list):
                    nxt.extend(child)
            else:
                nxt.append(child)
        values = nxt
    return values


def get_one(payload: Any, path: str, default: Any = None) -> Any:
    """The single value at a (non fan-out) path, or `default`."""
    values = get_path(payload, path)
    return values[0] if values else default


def parse_timestamp(value: Any) -> datetime | None:
    """An ISO-8601 timestamp as an aware datetime (naive ones are taken as UTC)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Violation:
    rule: str  # "schema" for a JSON Schema error, else the rule name
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "message": self.message}


# known_value lookups: (table, column, value) -> exists; supplied by the caller (data-ingest
# keeps a cache refreshed from the CDC tables), so this module needs no database access.
ReferenceLookup = Callable[[str, str, Any], bool]


def _check_rule(rule: ContractRule, payload: Any, lookup: ReferenceLookup | None, now: datetime) -> str | None:
    if rule.type == "sum_equals":
        items = get_path(payload, rule.items or "")
        totals = get_path(payload, rule.total or "")
        if not totals or not all(isinstance(v, int | float) for v in [*items, *totals]):
            return f"cannot compare {rule.items} with {rule.total}"
        if sum(items) != totals[0]:
            return f"sum of {rule.items} is {sum(items)}, {rule.total} is {totals[0]}"
        return None
    if rule.type == "nonzero":
        zeros = [v for v in get_path(payload, rule.field or "") if v == 0]
        return f"{len(zeros)} value(s) of {rule.field} are zero" if zeros else None
    if rule.type == "known_value":
        if lookup is None:
            return None  # reference data not loaded yet: don't quarantine on our own ignorance
        table, column = (rule.reference or "").split(".")
        unknown = [v for v in get_path(payload, rule.field or "") if not lookup(table, column, v)]
        return f"{rule.field}={unknown[0]!r} is not a known {table}.{column}" if unknown else None
    # not_future
    stamp = parse_timestamp(get_one(payload, rule.field or ""))
    if stamp is None:
        return f"{rule.field} is not a timestamp"
    if stamp > now + timedelta(seconds=rule.tolerance_seconds):
        return f"{rule.field} {stamp.isoformat()} is in the future"
    return None


class Contract:
    """A source's compiled JSON Schema + record rules."""

    def __init__(self, schema: dict[str, Any], rules: Iterable[ContractRule] = (), *, version: str = "1.0") -> None:
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self.rules = list(rules)
        self.version = version
        self.schema = schema

    @classmethod
    def for_source(cls, source: KafkaSource, scenario_dir: Path) -> Contract:
        schema = json.loads((scenario_dir / source.contract).read_text())
        version = str(schema.get("properties", {}).get("contract_version", {}).get("const", "1"))
        return cls(schema, source.rules, version=version)

    def check(
        self, payload: Any, *, lookup: ReferenceLookup | None = None, now: datetime | None = None
    ) -> list[Violation]:
        """Every violation of the schema, then of each rule (rules only run on a payload
        that passed the schema — their paths assume its shape)."""
        errors = sorted(self._validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
        if errors:
            return [
                Violation("schema", f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"[:300])
                for e in errors[:10]
            ]
        moment = now or datetime.now(UTC)
        violations = []
        for rule in self.rules:
            message = _check_rule(rule, payload, lookup, moment)
            if message is not None:
                violations.append(Violation(rule.name, message))
        return violations
