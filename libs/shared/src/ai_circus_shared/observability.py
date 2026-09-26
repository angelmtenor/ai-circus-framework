"""Observability wiring shared by every FastAPI service: Prometheus metrics + Langfuse trace tags.

Each `tabular_ml`/`conversational_rag`/`assisted_form` FastAPI service (prediction,
assistant, rag-agent, form-agent, platform-registry) calls `configure_metrics(app)`
once, right after constructing its `FastAPI` instance, to expose a `/metrics` endpoint
next to the existing `/healthz` route — no per-service boilerplate, no OpenTelemetry
collector to run locally. Uses `prometheus-fastapi-instrumentator`, which auto-tracks
request count/latency/in-progress gauges by method+path+status and needs no further
configuration for that baseline; add custom metrics at the call site if a service
later needs them.

GenAI tracing is *not* wired per service: every LLM call goes through llm-gateway,
whose LiteLLM `langfuse_otel` callback exports it to the platform's Langfuse
(k8s/base/langfuse.yaml). What each agent service does add is the request-body
`metadata` LiteLLM forwards to that callback, so a trace can be filtered by tenant,
scenario and conversation in the Langfuse UI — see `langfuse_request_metadata()`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator


def configure_metrics(app: FastAPI) -> None:
    """Instrument `app` and expose Prometheus-format metrics at `/metrics`.

    Idempotent per `app` instance — call once, immediately after `FastAPI(...)` is
    constructed, before any middleware/routers that should themselves be measured are
    added (the instrumentator wraps whatever is already on the app at expose-time).
    """
    Instrumentator().instrument(app).expose(app, include_in_schema=False)


def langfuse_request_metadata(
    *, service: str, org_id: str, scenario_slug: str, thread_id: str | None = None
) -> dict[str, Any]:
    """Build the `metadata` block LiteLLM's Langfuse callback reads off a completion request.

    Pass it as `ChatOpenAI(extra_body={"metadata": ...})` (langchain_openai's documented
    way to add proxy-specific body fields), on the per-request `model_copy()` the agent
    services already make to set `user=org_id`. LiteLLM's proxy lifts a top-level
    `metadata` dict into `litellm_params.metadata`, which its `langfuse_otel` logger maps
    onto Langfuse's trace fields: `trace_user_id` (the tenant, mirroring `user`),
    `session_id` (the conversation thread, so a multi-turn chat is one Langfuse session),
    `trace_name` and `tags` (service + scenario, for filtering). Keys are LiteLLM's, not
    Langfuse's — see litellm.integrations.langfuse.langfuse_otel._set_metadata_attributes.
    """
    metadata: dict[str, Any] = {
        "trace_user_id": org_id,
        "trace_name": f"{service}/{scenario_slug}",
        "tags": [service, f"scenario:{scenario_slug}", f"org:{org_id}"],
        "trace_metadata": {"service": service, "scenario_slug": scenario_slug, "org_id": org_id},
    }
    if thread_id:
        metadata["session_id"] = thread_id
    return metadata


# `?token=` / `?access_token=` values in a logged URL — the only way a browser can hand a
# bearer credential to a WebSocket (it cannot set headers), so agui-voice's `/ws/...`
# URLs carry one, and uvicorn logs every WebSocket handshake *with its query string*.
_TOKEN_QUERY_PARAM = re.compile(r"([?&](?:access_)?token=)[^&\s\"']+")


def _redact(value: object) -> object:
    return _TOKEN_QUERY_PARAM.sub(r"\1[REDACTED]", value) if isinstance(value, str) else value


class RedactTokenQueryParams(logging.Filter):
    """Blank bearer credentials out of logged URLs before any handler formats them."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact(arg) for arg in record.args)
        return True


def redact_token_query_params(logger_names: tuple[str, ...] = ("uvicorn.error", "uvicorn.access")) -> None:
    """Install `RedactTokenQueryParams` on uvicorn's loggers. Call before `uvicorn.run` —
    uvicorn's own logging dictConfig replaces handlers but keeps logger filters.
    Without it, an ADMIN_API_KEY or Keycloak JWT passed as `?token=` lands in the
    container log verbatim.
    """
    for name in logger_names:
        logging.getLogger(name).addFilter(RedactTokenQueryParams())
