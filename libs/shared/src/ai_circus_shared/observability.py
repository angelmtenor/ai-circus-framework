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
