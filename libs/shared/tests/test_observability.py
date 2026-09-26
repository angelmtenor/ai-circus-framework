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


def test_redact_token_query_params_blanks_bearer_credentials_in_uvicorn_log_records() -> None:
    import logging

    from ai_circus_shared.observability import RedactTokenQueryParams

    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '%s - "WebSocket %s" [accepted]',
        ("10.0.0.1:5", "/ws/churn?lang=en&token=angel2026&x=1"),
        None,
    )
    assert RedactTokenQueryParams().filter(record) is True
    assert record.getMessage() == '10.0.0.1:5 - "WebSocket /ws/churn?lang=en&token=[REDACTED]&x=1" [accepted]'
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "GET /x?access_token=eyJ.a.b", None, None)
    RedactTokenQueryParams().filter(record)
    assert record.getMessage() == "GET /x?access_token=[REDACTED]"
