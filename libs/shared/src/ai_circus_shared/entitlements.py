"""Client for services/platform-registry's entitlement and scenario-metadata API.

Every backend service calls `check_entitlement` before serving a scenario request —
enforcement happens at the API, not just in the UI. Both UIs call `list_scenarios`
to render only the scenarios a tenant is entitled to.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

# Every prediction/chat request otherwise made a fresh HTTP round-trip to
# platform-registry just to re-check the same (org, scenario) entitlement or
# re-fetch the same active model name — the dominant per-request latency cost
# under load. A short in-process TTL cache cuts that to ~one call per window per
# key; a just-revoked entitlement or just-switched model can take up to this long
# to take effect everywhere.
_CACHE_TTL_SECONDS = 30.0
_entitlement_cache: dict[tuple[str, str, str], tuple[bool, float]] = {}
_active_model_cache: dict[str, tuple[str, float]] = {}


class EntitlementDeniedError(Exception):
    """Raised when a tenant/org is not entitled to a scenario."""


class ScenarioSummary(BaseModel):
    """Scenario metadata as served by platform-registry (mirrors scenario.yaml).

    One consolidated `prediction`/`assistant`/`rag-agent` instance serves every
    scenario of its kind (routed by `{scenario_slug}` in the request path, e.g.
    `POST /predict/{slug}`), so there's no per-scenario service name to carry here —
    both UIs call one fixed configured URL per kind. `feature_columns`/`feature_schema`
    drive both UIs' generic tabular_ml form renderer, and `sample_questions` renders
    as clickable chat suggestions — all `None` for `conversational_rag` scenarios
    except `sample_questions`, which applies to both kinds.

    The single source of truth for this shape: platform-registry's API uses this
    directly as its `/entitlements/{org_id}` response_model (via `from_attributes`,
    straight off its `Scenario` ORM rows), and both UIs' `PlatformRegistryClient`
    parses the JSON response back into this same class.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    slug: str
    kind: str
    title: str
    description: str
    icon: str
    feature_columns: list[str] | None = None
    feature_schema: dict[str, Any] | None = None
    sample_questions: list[str] = []
    # tabular_ml only — lets both UIs render a plain "value units" prediction for
    # regression scenarios instead of the classification percentage/probability view.
    task_type: str | None = None
    target_units: str | None = None
    # tabular_ml only — the dataset column being predicted (not itself a feature).
    target: str | None = None


@dataclass(frozen=True)
class PlatformRegistryClient:
    """HTTP client for the platform-registry service."""

    base_url: str
    timeout_seconds: float = 5.0

    def check_entitlement(self, *, org_id: str, scenario_slug: str) -> None:
        """Raise `EntitlementDeniedError` unless the org is entitled to the scenario.

        Cached in-process for `_CACHE_TTL_SECONDS` (see module docstring comment).
        """
        cache_key = (self.base_url, org_id, scenario_slug)
        now = time.monotonic()
        cached = _entitlement_cache.get(cache_key)
        if cached is not None and cached[1] > now:
            entitled = cached[0]
        else:
            response = httpx.get(
                f"{self.base_url}/entitlements/{org_id}/{scenario_slug}",
                timeout=self.timeout_seconds,
            )
            entitled = response.status_code != httpx.codes.NOT_FOUND
            if entitled:
                response.raise_for_status()
            _entitlement_cache[cache_key] = (entitled, now + _CACHE_TTL_SECONDS)

        if not entitled:
            raise EntitlementDeniedError(f"Org {org_id!r} is not entitled to scenario {scenario_slug!r}.")

    def list_scenarios(self, *, org_id: str) -> list[ScenarioSummary]:
        """Return the scenarios the given org is entitled to."""
        response = httpx.get(f"{self.base_url}/entitlements/{org_id}", timeout=self.timeout_seconds)
        response.raise_for_status()
        return [ScenarioSummary(**item) for item in response.json()]

    def get_active_llm_model(self, *, admin_api_key: str) -> str:
        """Return the litellm_config.yaml model_name assistant/rag-agent should use for
        their next chat completion — the Settings page's live provider/model picker.
        Raises on failure (network/404/etc); callers decide whether to fall back to a
        static default. Cached in-process for `_CACHE_TTL_SECONDS`.
        """
        now = time.monotonic()
        cached = _active_model_cache.get(self.base_url)
        if cached is not None and cached[1] > now:
            return cached[0]

        response = httpx.get(
            f"{self.base_url}/llm-settings/active-model",
            headers={"Authorization": f"Bearer {admin_api_key}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        model_name = response.json()["model_name"]
        _active_model_cache[self.base_url] = (model_name, now + _CACHE_TTL_SECONDS)
        return model_name
