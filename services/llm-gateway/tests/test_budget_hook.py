"""Tests for the per-tenant AI Gateway budget enforcer (budget_hook.py) — exercised
against fakeredis (a real RESP-protocol server that just happens to run
in-process), the same convention ai_circus_shared.cache's own tests use.
"""

from __future__ import annotations

import asyncio

import fakeredis
import pytest
from fastapi import HTTPException

from llm_gateway import budget_hook


def _enforcer(client: fakeredis.FakeStrictRedis) -> budget_hook.BudgetEnforcer:
    """Bypass __init__ (which connects to a real CACHE_URL) — inject a fake client instead."""
    enforcer = budget_hook.BudgetEnforcer.__new__(budget_hook.BudgetEnforcer)
    enforcer._client = client
    return enforcer


@pytest.fixture
def client() -> fakeredis.FakeStrictRedis:
    return fakeredis.FakeStrictRedis(decode_responses=True)


def test_pre_call_hook_allows_a_request_with_no_org_id(client: fakeredis.FakeStrictRedis) -> None:
    """A request carrying no `user` field has no tenant context to meter — allowed."""
    result = asyncio.run(_enforcer(client).async_pre_call_hook(None, None, {}, "completion"))

    assert result is None


def test_pre_call_hook_allows_an_org_with_no_cap_configured(client: fakeredis.FakeStrictRedis) -> None:
    """Budgets are opt-in — an org nobody set a cap for is unlimited."""
    result = asyncio.run(_enforcer(client).async_pre_call_hook(None, None, {"user": "org-1"}, "completion"))

    assert result is None


def test_pre_call_hook_allows_spend_under_the_cap(client: fakeredis.FakeStrictRedis) -> None:
    client.set(budget_hook._tenant_key("org-1", budget_hook._CAP_KEY), "10.00")
    client.set(budget_hook._tenant_key("org-1", budget_hook._spend_key()), "5.00")

    result = asyncio.run(_enforcer(client).async_pre_call_hook(None, None, {"user": "org-1"}, "completion"))

    assert result is None


def test_pre_call_hook_blocks_spend_at_or_over_the_cap(client: fakeredis.FakeStrictRedis) -> None:
    client.set(budget_hook._tenant_key("org-1", budget_hook._CAP_KEY), "10.00")
    client.set(budget_hook._tenant_key("org-1", budget_hook._spend_key()), "10.00")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_enforcer(client).async_pre_call_hook(None, None, {"user": "org-1"}, "completion"))

    assert exc_info.value.status_code == 429
    assert "org-1" in exc_info.value.detail


def test_pre_call_hook_is_scoped_to_org(client: fakeredis.FakeStrictRedis) -> None:
    """org-1 being over budget must never block org-2."""
    client.set(budget_hook._tenant_key("org-1", budget_hook._CAP_KEY), "10.00")
    client.set(budget_hook._tenant_key("org-1", budget_hook._spend_key()), "10.00")

    result = asyncio.run(_enforcer(client).async_pre_call_hook(None, None, {"user": "org-2"}, "completion"))

    assert result is None


def test_log_success_event_accumulates_spend(client: fakeredis.FakeStrictRedis) -> None:
    enforcer = _enforcer(client)

    asyncio.run(enforcer.async_log_success_event({"user": "org-1", "response_cost": 0.15, "model": "gemini-flash"}, None, None, None))
    asyncio.run(enforcer.async_log_success_event({"user": "org-1", "response_cost": 0.10, "model": "gemini-flash"}, None, None, None))

    spend = client.get(budget_hook._tenant_key("org-1", budget_hook._spend_key()))
    assert float(spend) == pytest.approx(0.25)


def test_log_success_event_is_a_noop_without_an_org_id(client: fakeredis.FakeStrictRedis) -> None:
    asyncio.run(budget_hook.BudgetEnforcer.async_log_success_event(_enforcer(client), {"response_cost": 0.15}, None, None, None))

    assert client.keys("*") == []


def test_log_success_event_is_a_noop_without_a_cost(client: fakeredis.FakeStrictRedis) -> None:
    asyncio.run(_enforcer(client).async_log_success_event({"user": "org-1"}, None, None, None))

    assert client.keys("*") == []


def test_a_call_that_pushes_an_org_over_budget_is_not_itself_blocked(client: fakeredis.FakeStrictRedis) -> None:
    """The exact call that crosses the cap already completed by the time its cost
    is known — only the *next* call gets blocked. Confirms that ordering.
    """
    enforcer = _enforcer(client)
    client.set(budget_hook._tenant_key("org-1", budget_hook._CAP_KEY), "1.00")

    allowed_before = asyncio.run(enforcer.async_pre_call_hook(None, None, {"user": "org-1"}, "completion"))
    asyncio.run(enforcer.async_log_success_event({"user": "org-1", "response_cost": 1.50}, None, None, None))

    assert allowed_before is None
    with pytest.raises(HTTPException):
        asyncio.run(enforcer.async_pre_call_hook(None, None, {"user": "org-1"}, "completion"))
