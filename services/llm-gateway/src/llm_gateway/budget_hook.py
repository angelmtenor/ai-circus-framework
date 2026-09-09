"""
- Title:    Per-tenant monthly AI Gateway spend budgets
- Author:   ai-circus-framework contributors

litellm's own per-key/per-team budget enforcement needs its DB-backed proxy mode,
which needs prisma-client-py — archived upstream (2025-04-15, read-only, no more
releases), never added Python 3.13/3.14 support, and hangs importing on this
repo's Python 3.14 baseline (confirmed empirically — see
data_platform_manager.core.roadmap). This is the from-scratch alternative that
roadmap entry promised: a litellm `CustomLogger` (a stock, documented extension
point — https://docs.litellm.ai/docs/observability/custom_callback — no Prisma
involved) that checks/tracks spend directly in Valkey.

Not `ai_circus_shared.cache.TenantCache`: this service pins `fastapi==0.136.3`
exactly for litellm[proxy] compatibility (see pyproject.toml), while
ai-circus-shared requires fastapi>=0.141.1 — an unresolvable conflict. The key
format below (`tenant-{org_id}:{key}`) matches TenantCache's exactly, so the same
Valkey keys are readable/writable by data-platform-manager's admin endpoints
(which set the cap) and this hook (which reads the cap and writes spend).

Attribution: the calling services (assistant/rag-agent/form-agent) bind
`user=identity.org_id` onto their LangChain ChatOpenAI client per-request (never
baked into the shared, model-name-keyed cached client — see each service's
agui_endpoint) — `user` is a first-class OpenAI Chat Completions field, so it
lands in litellm's own request `data["user"]` untouched, as long as
`overwrite_user_with_key_hash` stays unset (it is, by default) and the client
already sends a `user` (which ours always do — litellm only fills `data["user"]`
from headers when the body omits it).

Monthly reset is free: the spend key is namespaced by the current UTC YYYY-MM, so
crossing into a new month starts a fresh key automatically — no cron/reset job.
An org with no cap configured (no `llm_budget_cap:<org>` key) is unlimited.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import redis
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth
from litellm.types.utils import CallTypesLiteral

from llm_gateway import get_env_config, get_logger

logger = get_logger(__name__)

# ~60 days: comfortably past any calendar month, just Valkey hygiene (the key
# naming itself is what makes the monthly reset work, not this TTL).
_SPEND_TTL_SECONDS = 60 * 60 * 24 * 60
_CAP_KEY = "llm_budget_cap"
_SPEND_KEY_PREFIX = "llm_budget_spend"


def _tenant_key(org_id: str, key: str) -> str:
    """Mirrors ai_circus_shared.cache.TenantCache._key exactly — see module
    docstring for why this hook can't just import that class.
    """
    return f"tenant-{org_id}:{key}"


def _spend_key() -> str:
    return f"{_SPEND_KEY_PREFIX}:{datetime.now(UTC):%Y-%m}"


class BudgetEnforcer(CustomLogger):
    """See module docstring. `litellm_config.yaml` references the module-level
    `instance` below via `litellm_settings.callbacks`, so this is built once at
    proxy startup — one Valkey connection shared across every request this
    process handles.
    """

    def __init__(self) -> None:
        """Connect to Valkey once, at import time."""
        super().__init__()
        self._client = redis.Redis.from_url(get_env_config().CACHE_URL, decode_responses=True)

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: Any,
        data: dict,
        call_type: CallTypesLiteral,
    ) -> dict | None:
        """Block the call with a 429 if this org already met/exceeded its monthly cap.

        An org with no cap configured, or a request with no `user` (org_id) at
        all, is never blocked — budgets here are opt-in.
        """
        org_id = data.get("user")
        if not org_id:
            return None
        cap = self._client.get(_tenant_key(org_id, _CAP_KEY))
        if cap is None:
            return None
        spend = float(self._client.get(_tenant_key(org_id, _spend_key())) or 0.0)
        if spend >= float(cap):
            from fastapi import HTTPException

            raise HTTPException(
                status_code=429,
                detail=(
                    f"Monthly AI Gateway budget exhausted for org={org_id!r} "
                    f"(spent ${spend:.2f} of ${float(cap):.2f})."
                ),
            )
        return None

    async def async_log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        """Add this call's real cost (litellm already computed it) to the org's
        running monthly total. Fires after the response completes, so the exact
        call that pushes an org over budget still succeeds — its cost isn't
        knowable before the response streams back; the *next* call is what gets
        blocked, by async_pre_call_hook above.
        """
        org_id = kwargs.get("user")
        cost = kwargs.get("response_cost")
        if not org_id or not cost:
            return
        full_key = _tenant_key(org_id, _spend_key())
        pipe = self._client.pipeline()
        pipe.incrbyfloat(full_key, float(cost))
        pipe.expire(full_key, _SPEND_TTL_SECONDS, nx=True)
        pipe.execute()
        logger.debug("org={} spend+=${:.4f} (model={})", org_id, cost, kwargs.get("model"))


instance = BudgetEnforcer()
