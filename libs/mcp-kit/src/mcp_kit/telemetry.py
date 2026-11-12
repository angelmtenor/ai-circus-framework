"""
telemetry.py
------------

What an MCP server built with the kit tells the platform (docs/adr/0023), following the observability
contract of the framework (ADR 0015):

- **metrics** on the process's default Prometheus registry, so the service's own `/metrics` serves them.
  Labels name the server, tool, outcome or reason only: never a tenant, a user or a value;
- **audit lines**: one JSON object per decision in the platform's log shape (`ts`, `level`, `service`,
  `logger`, `msg`, `trace_id`, `span_id`) plus ids and outcomes, so the collector parses them like any
  other log line and Grafana links each one to its trace.

OpenTelemetry is optional: without it (or without an active span) the trace fields are simply absent.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Histogram

TOOL_CALLS = Counter(
    "mcp_kit_tool_calls_total",
    "MCP tool calls by server, tool and outcome (ok, denied, error).",
    ["server", "tool", "outcome"],
)
TOOL_CALL_SECONDS = Histogram(
    "mcp_kit_tool_call_duration_seconds",
    "Duration of allowed MCP tool calls, including the API call.",
    ["server", "tool"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
ACCESS_REFUSED = Counter(
    "mcp_kit_access_refused_total",
    "Requests refused before any MCP handling: unauthenticated or inactive (401), forbidden (403), "
    "introspection_unavailable (503).",
    ["server", "reason"],
)
INTROSPECTIONS = Counter(
    "mcp_kit_introspections_total",
    "Live checks of the user's token at the identity provider (Level 3) by outcome: active, inactive, "
    "unavailable, cached.",
    ["server", "outcome"],
)
INTROSPECTION_SECONDS = Histogram(
    "mcp_kit_introspection_duration_seconds",
    "Round trip of a token introspection at the identity provider (cache hits excluded).",
    ["server"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)
TOKEN_EXCHANGES = Counter(
    "mcp_kit_token_exchanges_total",
    "Per-API token requests by outcome (issued, cached, refused).",
    ["server", "outcome"],
)
TOKEN_EXCHANGE_SECONDS = Histogram(
    "mcp_kit_token_exchange_duration_seconds",
    "Round trip of a token exchange at the identity provider (cache hits excluded).",
    ["server"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
API_RESPONSES = Counter(
    "mcp_kit_api_responses_total",
    "Responses of the API behind the server, by status class.",
    ["server", "status_class"],
)


def current_trace_ids() -> dict[str, str]:
    """`trace_id` and `span_id` of the active span, when OpenTelemetry is installed and tracing."""
    try:
        from opentelemetry import trace  # optional dependency
    except ImportError:
        return {}
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}


def audit_record(event: str, *, server: str, level: str = "INFO", **fields: Any) -> str:
    """One audit line in the platform's log shape. Callers pass ids, names and outcomes only."""
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "level": level,
        "service": server,
        "logger": "mcp_kit.audit",
        "msg": event,
        "event": event,
        "server": server,
        **fields,
        **current_trace_ids(),
    }
    return json.dumps(record, sort_keys=True)


def log_audit(logger: logging.Logger, event: str, *, server: str, level: str = "INFO", **fields: Any) -> None:
    """Write one audit line through `logger` (see `configure_audit_output`)."""
    logger.log(
        logging.WARNING if level == "WARNING" else logging.INFO,
        audit_record(event, server=server, level=level, **fields),
    )
