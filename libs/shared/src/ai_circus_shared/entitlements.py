"""Client for services/platform-registry's entitlement and scenario-metadata API.

Every backend service calls `check_entitlement` before serving a scenario request —
enforcement happens at the API, not just in the UI. Both UIs call `list_scenarios`
to render only the scenarios a tenant is entitled to.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class EntitlementDeniedError(Exception):
    """Raised when a tenant/org is not entitled to a scenario."""


@dataclass(frozen=True)
class ScenarioSummary:
    """Scenario metadata as served by platform-registry (mirrors scenario.yaml)."""

    slug: str
    kind: str
    title: str
    description: str
    icon: str


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
