"""
- Title:    Record gateway budget changes in platform-registry's admin audit trail
- Author:   Angel Martinez-Tenor

platform-registry is the single writer of the hash-chained `audit_log` (and emits the
matching governance event), so this service — which owns the gateway budgets — reports
its mutations there with the caller's own admin bearer token.

Off unless `GOVERNANCE_AUDIT_ENABLED` is true. Failure semantics are explicit: the budget
change has already been applied when this runs, so a registry outage is **fail-open** —
the change stands, and the failure is logged at WARNING (never silent). Nothing here is
sent but IDs and the cap amounts.
"""

from __future__ import annotations

from typing import Any

import httpx
from ai_circus_shared.governance_events import env_flag

from data_platform_manager import get_env_config, get_logger

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 3.0


def _config() -> object:
    try:
        return get_env_config()
    except Exception:  # config invalid: start-up validation reports it, not a request
        return object()


def audit_enabled() -> bool:
    """Whether budget changes are reported to platform-registry (GOVERNANCE_AUDIT_ENABLED)."""
    return env_flag(getattr(_config(), "GOVERNANCE_AUDIT_ENABLED", None))


def record_budget_change(
    *,
    authorization: str | None,
    action: str,
    org_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> bool:
    """Report one budget mutation; returns True when platform-registry recorded it.

    `action` is `gateway.budget.set` or `gateway.budget.deleted`. Never raises.
    """
    if not audit_enabled():
        return False
    base_url = getattr(_config(), "PLATFORM_REGISTRY_URL", None)
    if not base_url or not authorization:
        logger.warning("Budget change for org={} not audited: PLATFORM_REGISTRY_URL or credentials missing", org_id)
        return False
    body = {
        "action": action,
        "target_type": "budget",
        "target_id": org_id,
        "org_id": org_id,
        "before": before,
        "after": after,
    }
    try:
        response = httpx.post(
            f"{base_url}/governance/audit",
            json=body,
            headers={"Authorization": authorization},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Budget change for org={} NOT audited (fail-open): {}", org_id, type(exc).__name__)
        return False
    return True
