"""Client for services/platform-registry's entitlement and scenario-metadata API.

Every backend service calls `check_entitlement` before serving a scenario request —
enforcement happens at the API, not just in the UI. Both UIs call `list_scenarios`
to render only the scenarios a tenant is entitled to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


class EntitlementDeniedError(Exception):
    """Raised when a tenant/org is not entitled to a scenario."""


@dataclass(frozen=True)
class ScenarioSummary:
    """Scenario metadata as served by platform-registry (mirrors scenario.yaml).

    One consolidated `prediction`/`assistant`/`rag-agent` instance serves every
    scenario of its kind (routed by `{scenario_slug}` in the request path, e.g.
    `POST /predict/{slug}`), so there's no per-scenario service name to carry here —
    both UIs call one fixed configured URL per kind. `feature_columns`/`feature_schema`
    drive both UIs' generic tabular_ml form renderer, and `sample_questions` renders
    as clickable chat suggestions — all `None` for `conversational_rag` scenarios
    except `sample_questions`, which applies to both kinds.
    """

    slug: str
    kind: str
    title: str
    description: str
    icon: str
    feature_columns: list[str] | None = None
    feature_schema: dict[str, Any] | None = None
    sample_questions: list[str] = field(default_factory=list)
    # tabular_ml only — lets both UIs render a plain "value units" prediction for
    # regression scenarios instead of the classification percentage/probability view.
    task_type: str | None = None
    target_units: str | None = None


@dataclass(frozen=True)
class PlatformRegistryClient:
    """HTTP client for the platform-registry service."""

    base_url: str
    timeout_seconds: float = 5.0

    def check_entitlement(self, *, org_id: str, scenario_slug: str) -> None:
        """Raise `EntitlementDeniedError` unless the org is entitled to the scenario."""
        response = httpx.get(
            f"{self.base_url}/entitlements/{org_id}/{scenario_slug}",
            timeout=self.timeout_seconds,
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            raise EntitlementDeniedError(f"Org {org_id!r} is not entitled to scenario {scenario_slug!r}.")
        response.raise_for_status()

    def list_scenarios(self, *, org_id: str) -> list[ScenarioSummary]:
        """Return the scenarios the given org is entitled to."""
        response = httpx.get(f"{self.base_url}/entitlements/{org_id}", timeout=self.timeout_seconds)
        response.raise_for_status()
        return [ScenarioSummary(**item) for item in response.json()]

    def get_active_llm_model(self, *, admin_api_key: str) -> str:
        """Return the litellm_config.yaml model_name assistant/rag-agent should use for
        their next chat completion — the Settings page's live provider/model picker.
        Raises on failure (network/404/etc); callers decide whether to fall back to a
        static default.
        """
        response = httpx.get(
            f"{self.base_url}/llm-settings/active-model",
            headers={"Authorization": f"Bearer {admin_api_key}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["model_name"]
