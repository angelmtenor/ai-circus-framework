"""Client for services/platform-registry's entitlement and scenario-metadata API.

Every backend service calls `check_entitlement` before serving a scenario request —
enforcement happens at the API, not just in the UI. Both UIs call `list_scenarios`
to render only the scenarios a tenant is entitled to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class EntitlementDeniedError(Exception):
    """Raised when a tenant/org is not entitled to a scenario."""


@dataclass(frozen=True)
class ScenarioSummary:
    """Scenario metadata as served by platform-registry (mirrors scenario.yaml).

    `prediction_service`/`assistant_service`/`agent_service` are the compose service
    names implementing this scenario — both UIs build request URLs from these
    (`http://<service>.localhost`) instead of a single hardcoded global endpoint, since
    multiple scenarios of the same kind can each run their own dedicated instance of
    the same generic image (see docker-compose.yml, e.g. `prediction` vs
    `prediction-mpm`). `feature_columns`/`feature_schema` drive both UIs' generic
    tabular_ml form renderer — `None` for `conversational_rag` scenarios.
    """

    slug: str
    kind: str
    title: str
    description: str
    icon: str
    prediction_service: str | None = None
    assistant_service: str | None = None
    agent_service: str | None = None
    feature_columns: list[str] | None = None
    feature_schema: dict[str, Any] | None = None


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
