"""Tests for ai_circus_shared.observability's Langfuse request-metadata helper."""

from __future__ import annotations

from ai_circus_shared.observability import langfuse_request_metadata


def test_metadata_carries_tenant_scenario_and_session() -> None:
    metadata = langfuse_request_metadata(service="assistant", org_id="acme", scenario_slug="churn", thread_id="t-1")
    assert metadata["trace_user_id"] == "acme"
    assert metadata["session_id"] == "t-1"
    assert metadata["trace_name"] == "assistant/churn"
    assert metadata["tags"] == ["assistant", "scenario:churn", "org:acme"]
    assert metadata["trace_metadata"] == {"service": "assistant", "scenario_slug": "churn", "org_id": "acme"}


def test_metadata_omits_session_without_a_thread() -> None:
    metadata = langfuse_request_metadata(service="rag-agent", org_id="acme", scenario_slug="docs", thread_id=None)
    assert "session_id" not in metadata
    assert metadata["trace_user_id"] == "acme"
