"""
- Title:    Policy decision contract (input / output) for the gateway's decision point
- Author:   Angel Martinez-Tenor

llm-gateway asks a *policy decision point* whether a model call may proceed, and enforces the
answer. The point can be anything that speaks this JSON contract — a built-in static file, an
OPA policy, or any HTTP service an external governance tool provides — so the framework never
depends on a particular product. These models are the contract's source of truth for the
published schemas (`docs/governance/schemas/policy-input.json`, `policy-decision.json`);
llm-gateway cannot import this package (litellm pins fastapi), so it carries its own small
parser and a gateway contract test validates it against the same schemas.

The input deliberately carries no request content: tenant, AI system, model and call type only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PolicyInput(BaseModel):
    """What the decision point is asked about."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(max_length=64)
    ai_system_id: str = Field(max_length=64)
    model: str = Field(max_length=128)
    call_type: str = Field(max_length=64, description="LiteLLM call type, e.g. completion or embedding")


class Obligations(BaseModel):
    """What the gateway must enforce alongside the decision."""

    model_config = ConfigDict(extra="forbid")

    # Models this AI system may use; a call to another model is denied (or, on `degrade`,
    # served by the first listed one).
    allowed_models: list[str] | None = Field(default=None, max_length=50)
    max_tokens: int | None = Field(default=None, ge=1, description="Cap on completion tokens")
    rpm: int | None = Field(default=None, ge=1, description="Requests per minute for this tenant + AI system")
    monthly_budget: float | None = Field(default=None, ge=0, description="USD per month for this tenant + AI system")
    status: Literal["active", "suspended"] = "active"


class PolicyDecision(BaseModel):
    """The decision point's answer."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "deny", "degrade"]
    reason_code: str = Field(pattern=r"^[a-z0-9_]{1,96}$")
    policy_version: str | None = Field(default=None, max_length=64)
    obligations: Obligations = Field(default_factory=Obligations)
