"""
- Title:    Tests for the declared AI-system governance block
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_circus_shared.governance import GovernanceConfig, ai_system_id_of, resolve_governance
from ai_circus_shared.scenario_schema import load_all

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_FILE = _REPO_ROOT / "docs" / "governance" / "schemas" / "ai-system-governance.json"


def test_empty_block_is_valid_and_declares_nothing() -> None:
    config = GovernanceConfig()
    assert config.ai_act.risk_category == "unassessed"
    assert config.ai_act.role is None
    assert config.human_oversight.mode is None
    assert config.data.personal_data is None


def test_full_block_round_trips() -> None:
    config = GovernanceConfig.model_validate(
        {
            "ai_system_id": "loan-triage.v1",
            "intended_purpose": "Triage",
            "owner": "Team",
            "ai_act": {"role": "deployer", "risk_category": "high", "annex_iii_area": "essential_services"},
            "human_oversight": {"mode": "approve", "description": "A person confirms."},
            "data": {"personal_data": True, "special_categories": False, "datasets": ["d1"]},
            "limitations": ["a"],
            "known_risks": ["b"],
        }
    )
    assert config.ai_act.annex_iii_area == "essential_services"
    assert GovernanceConfig.model_validate(config.model_dump()) == config


@pytest.mark.parametrize(
    "bad",
    [
        {"ai_act": {"risk_category": "compliant"}},
        {"ai_act": {"role": "user"}},
        {"ai_act": {"annex_iii_area": "marketing"}},
        {"human_oversight": {"mode": "always"}},
        {"ai_system_id": "Has Space"},
        {"ai_system_id": "a:b"},
        {"ai_system_id": "../x"},
        {"unknown_field": 1},
    ],
)
def test_invalid_blocks_are_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        GovernanceConfig.model_validate(bad)


def test_resolve_defaults_ai_system_id_to_the_slug_and_flags_undeclared() -> None:
    record = resolve_governance("titanic", None)
    assert record["ai_system_id"] == "titanic"
    assert record["declared"] is False
    assert record["ai_act"] == {"role": None, "risk_category": "unassessed", "annex_iii_area": None}


def test_resolve_keeps_an_explicit_ai_system_id() -> None:
    record = resolve_governance("titanic", GovernanceConfig(ai_system_id="voyage-model"))
    assert record["ai_system_id"] == "voyage-model"
    assert record["declared"] is True


def test_every_repo_scenario_still_loads_and_resolves_a_valid_ai_system_id() -> None:
    definitions = load_all(_REPO_ROOT / "scenarios")
    assert definitions
    seen: set[str] = set()
    for definition in definitions:
        record = resolve_governance(definition.slug, definition.governance)
        # The resolved id must itself satisfy the pattern (slugs are used as the default).
        GovernanceConfig(ai_system_id=str(record["ai_system_id"]))
        seen.add(str(record["ai_system_id"]))
    assert len(seen) == len(definitions), "ai_system_id must be unique per scenario"


def test_published_json_schema_matches_the_model() -> None:
    """Regenerate with: uv run python -c "import json; from ai_circus_shared.governance import
    GovernanceConfig as G; print(json.dumps(G.model_json_schema(), indent=2, sort_keys=True))"
    > docs/governance/schemas/ai-system-governance.json
    """
    published = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))
    assert published == json.loads(json.dumps(GovernanceConfig.model_json_schema(), sort_keys=True))


def test_ai_system_id_of_prefers_the_declared_id_and_falls_back_to_the_slug() -> None:
    definitions = {d.slug: d for d in load_all(_REPO_ROOT / "scenarios")}
    assert ai_system_id_of(definitions["churn"]) == "churn"
    assert ai_system_id_of(definitions["prestaciones_sociales"]) == "prestaciones_sociales"

    class Declared:
        slug = "churn"
        governance = GovernanceConfig(ai_system_id="churn-v2")

    assert ai_system_id_of(Declared()) == "churn-v2"
