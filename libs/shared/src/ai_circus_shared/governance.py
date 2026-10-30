"""
- Title:    Declared AI-system governance metadata (the inventory foundation)
- Author:   Angel Martinez-Tenor

The optional `governance:` block of a `scenario.yaml`: what the *owner declares* about an
AI system so an external governance tool can inventory it — purpose, owner, EU AI Act
role / risk category, human oversight, data categories, limitations and known risks.

This is declared metadata, never an assessment: the framework supports an AI
management system, it is not one, and nothing here classifies legal risk automatically
(`risk_category` stays `unassessed` until a person decides otherwise). The block is
optional, and a scenario without one behaves exactly as before — it only gets the stable
`ai_system_id` (its slug) and `declared: false` in what platform-registry serves.

`ai_system_id` is the key every later governance contract hangs on (policy decisions,
trace correlation attributes, events), so it is a Valkey-key-safe token, never free text.
The JSON Schema of `GovernanceConfig` is published as
`docs/governance/schemas/ai-system-governance.json` (a test keeps the two in sync).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Safe inside a Valkey key and a URL path segment: no whitespace, `:` or `/`.
AI_SYSTEM_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,63}$"

AiActRole = Literal["provider", "deployer"]
AiActRiskCategory = Literal["prohibited", "high", "limited", "minimal", "unassessed"]
# The eight areas of EU AI Act Annex III (high-risk use cases), as stable snake_case keys.
AnnexIiiArea = Literal[
    "biometrics",
    "critical_infrastructure",
    "education",
    "employment",
    "essential_services",
    "law_enforcement",
    "migration_border",
    "justice_democracy",
]
OversightMode = Literal["none", "review", "approve"]


class AiActInfo(BaseModel):
    """Where the owner places this system under the EU AI Act — declared, not computed."""

    model_config = ConfigDict(extra="forbid")

    role: AiActRole | None = None
    risk_category: AiActRiskCategory = "unassessed"
    annex_iii_area: AnnexIiiArea | None = None


class HumanOversight(BaseModel):
    """How a person stays in the loop: `review` = checks outcomes after the fact,
    `approve` = a person must confirm before an outcome takes effect.
    """

    model_config = ConfigDict(extra="forbid")

    mode: OversightMode | None = None
    description: str | None = Field(default=None, max_length=1000)


class GovernanceData(BaseModel):
    """What kind of data the system touches; `None` = not declared (not "no")."""

    model_config = ConfigDict(extra="forbid")

    personal_data: bool | None = None
    special_categories: bool | None = None
    datasets: list[str] = Field(default_factory=list, max_length=50)


class GovernanceConfig(BaseModel):
    """The whole `governance:` block. Every field is optional."""

    model_config = ConfigDict(extra="forbid")

    # Stable identity of the AI system; defaults to the scenario slug (see resolve_governance).
    ai_system_id: str | None = Field(default=None, pattern=AI_SYSTEM_ID_PATTERN)
    intended_purpose: str | None = Field(default=None, max_length=1000)
    owner: str | None = Field(default=None, max_length=200)
    ai_act: AiActInfo = Field(default_factory=AiActInfo)
    human_oversight: HumanOversight = Field(default_factory=HumanOversight)
    data: GovernanceData = Field(default_factory=GovernanceData)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    known_risks: list[str] = Field(default_factory=list, max_length=30)


def resolve_governance(slug: str, declared: GovernanceConfig | None) -> dict[str, Any]:
    """The governance record served for a scenario: the declared block (or an empty one)
    with `ai_system_id` defaulted to the slug, plus `declared` — whether the YAML had a
    block at all, so a consumer can tell "nothing declared" from "declared as minimal".
    """
    config = declared if declared is not None else GovernanceConfig()
    record = config.model_dump()
    record["ai_system_id"] = config.ai_system_id or slug
    record["declared"] = declared is not None
    return record


def ai_system_id_of(definition: object) -> str:
    """The scenario's stable AI-system id: the declared `governance.ai_system_id`, else its slug.

    Takes anything with `.slug` and an optional `.governance` (a `ScenarioDefinition`), so the
    agent services can tag their gateway calls without importing the scenario schema here.
    """
    governance = getattr(definition, "governance", None)
    declared = getattr(governance, "ai_system_id", None)
    return str(declared or definition.slug)  # type: ignore[attr-defined]
